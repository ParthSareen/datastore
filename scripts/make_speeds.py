#!/usr/bin/env python
import argparse
import random
import os
import errno
import sys
import logging as log

try:
  import segment_pb2
  import tile_pb2
  import speedtile_pb2
except ImportError:
  print 'You need to generate protobuffer source via: protoc --python_out . --proto_path ../proto ../proto/*.proto'
  sys.exit(1)

try:
  import flatbuffers
  from dsfb.Histogram import Histogram
  from dsfb.Segment import Segment
  from dsfb.Entry import Entry
  from dsfb.VehicleType import VehicleType
except ImportError:
  print 'You need to generate the flatbuffer source via: sed -e "s/namespace.*/namespace dsfb;/g" ../src/main/fbs/histogram-tile.fbs > schema.fbs && flatc --python schema.fbs'
  sys.exit(1)



#try this fat tile: wget https://s3.amazonaws.com/datastore_output_prod/2017/1/1/0/0/2415.fb
###############################################################################
LEVEL_BITS = 3
TILE_INDEX_BITS = 22
SEGMENT_INDEX_BITS = 21

LEVEL_MASK = (2**LEVEL_BITS) - 1
TILE_INDEX_MASK = (2**TILE_INDEX_BITS) - 1
SEGMENT_INDEX_MASK = (2**SEGMENT_INDEX_BITS) - 1

def get_level(segment_id):
  return segment_id & LEVEL_MASK
def get_tile_index(segment_id):
  return (segment_id >> LEVEL_BITS) & TILE_INDEX_MASK
def get_segment_index(segment_id):
  return (segment_id >> (LEVEL_BITS + TILE_INDEX_BITS)) & SEGMENT_INDEX_MASK


###############################################################################
#the step sizes, which increase as the high 2 bits increase to provide variable precision.
STEP_SIZES = [ 1, 2, 5, 10 ]#offset of each step, derived from the above.
#STEP_OFFSET[i] = STEP_OFFSET[i-1] + 2^6 * STEP_SIZES[i-1]
STEP_OFFSETS = [ 0, 64, 192, 512, 1152 ]

def unquantise(val):
  hi = 0
  lo = 0
  if val < 0:
    hi = 2 | (((-val) & 64) >> 6)
    lo = (-val) & 63
  else:
    hi = (val & 192) >> 6
    lo = val & 63
  if hi >= 0 and hi < 4 and lo >= 0 and lo < 64:
    return STEP_OFFSETS[hi] + STEP_SIZES[hi] * lo
  raise (hi, lo)

###############################################################################
def getSegments(path, target_level, target_tile_id, lengths):
  log.debug('getSegments ###############################################################################')
  log.debug('Looking for level=' + str(target_level) + ' and tile_id=' + str(target_tile_id) + ' here:' + path)
  segments = {}
  for root, dirs, files in os.walk(path):
    for file in files:
      if (root + os.sep + file).endswith('.fb'):
        with open(root + os.sep + file, 'rb') as filehandle:
          print 'Loading ' + (root + os.sep + file) + '...'
          hist = Histogram.GetRootAsHistogram(bytearray(filehandle.read()), 0)
        level = get_level(hist.TileId())
        tile_index = get_tile_index(hist.TileId())
        if (level == target_level) and (tile_index == target_tile_id):
          print 'Processing ' + (root + os.sep + file) + '...'
          #for each segment
          for i in range(0, hist.SegmentsLength()):
            segment = hist.Segments(i)
            #has to be one we know about and its not tombstoned/markered
            if segment.EntriesLength() > 0 and segment.SegmentId() < len(lengths) and lengths[segment.SegmentId()] > 0:
              length = lengths[segment.SegmentId()]
              processSegment(segments, segment, length)
        del hist
  return segments

###############################################################################
def processSegment(segments, segment, length):
  for i in range(0, segment.EntriesLength()):
    e = segment.Entries(i)
    #get the right segment
    if segment.SegmentId() not in segments:
      segments[segment.SegmentId()] = { }
    hours = segments[segment.SegmentId()]
    #get the right hour in there
    if e.EpochHour() not in hours:
       hours[e.EpochHour()] = { }
    nexts = hours[e.EpochHour()]
    #if you dont have the right next segment in there
    if segment.NextSegmentIds(e.NextSegmentIdx()) not in nexts:
      nexts[segment.NextSegmentIds(e.NextSegmentIdx())] = {'count': 0, 'duration': 0, 'queue': 0 }
    totals = nexts[segment.NextSegmentIds(e.NextSegmentIdx())]
    #continuing a previous pair
    totals['count'] += e.Count()
    totals['duration'] += unquantise(e.DurationBucket()) * e.Count()
    totals['queue'] += (e.Queue()/255.0) * length * e.Count()

###############################################################################
# length in meters, rounded to the nearest meter
def getLengths(fileName):
  osmlr = tile_pb2.Tile()
  with open(fileName, 'rb') as f:
    osmlr.ParseFromString(f.read())

  #get out the length
  lengths = []
  for entry in osmlr.entries:
    length = 0
    if entry.segment:
      for loc_ref in entry.segment.lrps:
        if loc_ref.length:
          length = length + loc_ref.length

      lengths.append(length)
    else:
      lengths.append(-1)

  del osmlr
  return lengths

###############################################################################
def remove(path):
  try:
    os.remove(path)
  except OSError as e:
    if e.errno != errno.ENOENT:
      raise

###############################################################################
def write(name, count, tile, should_remove):
  name += '.' + str(count)
  if should_remove:
    remove(name)
  with open(name, 'ab') as f:
    f.write(tile.SerializeToString())

###############################################################################
def next(startIndex, total, nextName, subtileSegments):
  tile = speedtile_pb2.SpeedTile()
  subtile = tile.subtiles.add()
  if nextName:
    nextTile = speedtile_pb2.SpeedTile()
    nextSubtile = nextTile.subtiles.add()
  else:
    nextTile = tile
    nextSubtile = subtile
  for st in [subtile, nextSubtile]:
    #geo stuff
    st.level = args.level      #TODO: get from osmlr
    st.index = args.tile_id   #TODO: get from osmlr
    st.startSegmentIndex = startIndex
    st.totalSegments = total
    st.subtileSegments = subtileSegments
    #time stuff
    st.rangeStart = 1483228800 #TODO: get from input
    st.rangeEnd = 1483833600   #TODO: get from input
    st.unitSize = 604800       #TODO: get from input
    st.entrySize = 3600        #TODO: get from input
    st.description = '168 ordinal hours of week 0 of year 2017' #TODO: get from input
  return tile, subtile, nextTile, nextSubtile

###############################################################################
#method simulates generation of speed data by populating with random data
def simulate(lengths, fileName, subTileSize, nextName, separate):
  random.seed(0)

  #fake a segment for each entry in the osmlr
  tile = None
  nextTile = None
  subTileCount = 0
  first = True
  for k, sid in enumerate(lengths):
    #its time to write a subtile
    if k % subTileSize == 0:
      #writing tile
      if tile is not None:
        write(fileName, subTileCount, tile, first or separate)
        #writing next data if its separated
        if nextTile is not tile:
          write(nextName, subTileCount, nextTile, first or separate)
        #dont delete the files from this point on
        first = False
        #if the subtiles are to be separate increment
        if separate:
          subTileCount += 1
        #release all memory
        del subtile
        del tile
        del nextSubtile
        del nextTile
      #set up new pbf messages to write into
      tile, subtile, nextTile, nextSubtile = next(k, len(lengths), nextName, subTileSize)

    #continue making fake data
    subtile.referenceSpeeds.append(random.randint(20, 100) if sid != -1 else 0)
    #dead osmlr ids have no next segment data
    nextIds = [ (random.randint(0,2**21)<<25)|(subtile.index<<3)|subtile.level for i in range(0, random.randint(0,3)) ] if sid != -1 else []
    #do all the entries
    for i in range(0, subtile.unitSize/subtile.entrySize):
      #any time its a dead one we put in 0's for the data
      subtile.speeds.append(random.randint(20, 100) if sid != -1 else 0)
      subtile.speedVariances.append(int(random.uniform(0,127.5) * 2 if sid != -1 else 0))
      subtile.prevalences.append(random.randint(1, 100) if sid != -1 else 0)
      subtile.nextSegmentIndices.append(len(subtile.nextSegmentIds) if sid != -1 else 0)
      subtile.nextSegmentCounts.append(len(nextIds) if sid != -1 else 0)
      for nid in nextIds:
        nextSubtile.nextSegmentIds.append(nid)
        nextSubtile.nextSegmentDelays.append(random.randint(0,30))
        nextSubtile.nextSegmentDelayVariances.append(int(random.uniform(0,100)))
        nextSubtile.nextSegmentQueueLengths.append(random.randint(0,200))
        nextSubtile.nextSegmentQueueLengthVariances.append(int(random.uniform(0,200)))

  #get the last one written
  if tile is not None:
    write(fileName, subTileCount, tile, first or separate)
    if nextTile is not tile:
      write(nextName, subTileCount, nextTile, first or separate)
    del subtile
    del tile
    del nextSubtile
    del nextTile

###############################################################################
#TODO: figure out how to measure this for real
def prevalence(val):
  return int(round(val / 10.0) * 10)

###############################################################################
# calculate and return the variance of the specified list
def variance(items):
  # calculate mean
  mean = sum(items) / float(len(items))
  # calculate and return the variance
  return int(round(sum([(xi - mean)**2 for xi in items]) / len(items)))

###############################################################################
#method simulates generation of speed data by populating with real data from osmlr
#and reporter results converted to fb output
def createSpeedTiles(lengths, fileName, subTileSize, nextName, separate, segments):
  log.debug('createSpeedTiles ###############################################################################')

  #find the minimum hour
  minHour = min([int(hour) for k,v in segments.iteritems() for hour in v.keys()])
  log.debug('minHour=' + str(minHour))

  #fake a segment for each entry in the osmlr
  tile = None
  nextTile = None
  subTileCount = 0
  first = True
  for k, length in enumerate(lengths):
    #its time to write a subtile
    if k % subTileSize == 0:
      #writing tile
      if tile is not None:
        write(fileName, subTileCount, tile, first or separate)
        #writing next data if its separated
        if nextTile is not tile:
          write(nextName, subTileCount, nextTile, first or separate)
        #dont delete the files from this point on
        first = False
        #if the subtiles are to be separate increment
        if separate:
          subTileCount += 1
        #release all memory
        del subtile
        del tile
        del nextSubtile
        del nextTile
      #set up new pbf messages to write into
      tile, subtile, nextTile, nextSubtile = next(k, len(lengths), nextName, subTileSize)


    #TODO
    #subtile.referenceSpeeds.append(random.randint(20, 100) if length > 0 else 0)

    #do all the entries
    for i in range(0 + minHour, subtile.unitSize/subtile.entrySize + minHour):
      #if we have data get it
      nextSegments = segments[k][i] if k in segments and i in segments[k] else None
      #compute the averages
      if nextSegments:
        for nid, n in nextSegments.iteritems():
          n['duration'] /= float(n['count'])
          n['queue'] /= float(n['count'])

      # create speed list in kph instead of meters per second
      if nextSegments:
        speeds = [int(round(length / n['duration'] * 3.6)) for nid, n in nextSegments.iteritems()]

      #any time its a dead one we put in 0's for the data
      minDuration = min([n['duration'] for nid, n in nextSegments.iteritems()]) if nextSegments else 0
      # assign speed in kph
      subtile.speeds.append(max(speeds) if nextSegments else 0)

      if nextSegments:
        log.debug('segmentId=' + str((k<<25)|(args.tile_id<<3)|args.level) + ' | nextSegments=' + str(nextSegments) + ' | length=' + str(length) + ' | minDuration=' + str(minDuration) + ' | speed=' + str(max(speeds)) + ' | varSpeed=' + str(variance(speeds)))

      subtile.speedVariances.append(variance(speeds) if nextSegments else 0)
      subtile.prevalences.append(prevalence(sum([n['count'] for nid, n in nextSegments.iteritems()]) if nextSegments else 0))
      subtile.nextSegmentIndices.append(len(subtile.nextSegmentIds) if 1 else 0)
      subtile.nextSegmentCounts.append(len(nextSegments) if nextSegments else 0)

      if nextSegments:
        # create delay list
        delays = [int(round(n['duration'] - minDuration)) for nid, n in nextSegments.iteritems()]
        # create queue length list
        queueLengths = [float(n['queue']) for nid, n in nextSegments.iteritems()]
        # assign next segment attributes
        for nid, n in nextSegments.iteritems():
          nextSubtile.nextSegmentIds.append(nid)
          nextSubtile.nextSegmentDelays.append(int(round(n['duration'] - minDuration)))
          nextSubtile.nextSegmentDelayVariances.append(variance(delays))
          nextSubtile.nextSegmentQueueLengths.append(int(round(n['queue'])))
          nextSubtile.nextSegmentQueueLengthVariances.append(variance(queueLengths))

  #get the last one written
  if tile is not None:
    write(fileName, subTileCount, tile, first or separate)
    if nextTile is not tile:
      write(nextName, subTileCount, nextTile, first or separate)
    del subtile
    del tile
    del nextSubtile
    del nextTile


#Read in OSMLR & flatbuffer tiles from the datastore output in AWS to read in the lengths, speeds & next segment ids and generate the segment speed files in proto output format
if __name__ == "__main__":
  parser = argparse.ArgumentParser(description='Generate fake speed tiles', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser.add_argument('--output-prefix', type=str, help='The file name prefix to give to output tiles. The first tile will have no suffix, after that they will be numbered starting at 1. e.g. tile.spd, tile.spd.1, tile.spd.2', default='tile.spd')
  parser.add_argument('--max-segments', type=int, help='The maximum number of segments to have in a single subtile message', default=10000)
  parser.add_argument('--no-separate-subtiles', help='If present all subtiles will be in the same tile', action='store_true')
  parser.add_argument('--separate-next-segments-prefix', type=str, help='The prefix for the next segments output tiles if they should be separated from the primary speed entries. If omitted they will not be separate')
  parser.add_argument('--osmlr', type=str, help='The osmlr tile containing the relevant segments definitions')
  parser.add_argument('--fb-path', type=str, help='The flatbuffer tile path to load the files necessary for the time period given')
  parser.add_argument('--level', type=int, help='The level to target')
  parser.add_argument('--tile-id', type=int, help='The tile id to target')
  parser.add_argument('--verbose', '-v', help='Turn on verbose output i.e. DEBUG level logging', action='store_true')
  #TODO: add the time period argument
  args = parser.parse_args()

  if args.verbose:
    log.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout, level=log.DEBUG)
  else:
    log.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)

  print 'getting osmlr lengths'
  lengths = getLengths(args.osmlr)

  print 'getting speed averages from fb Histogram'
  segments = getSegments(args.fb_path, args.level, args.tile_id, lengths)

  if args.verbose:
    log.debug('loop over segments ###############################################################################')
    for k,v in segments.iteritems():
      log.debug('k=' + str(k) + ' | v=' + str(v))
    log.debug('DONE loop over segments ###############################################################################')

  #print 'simulating 1 week of speeds at hourly intervals for ' + str(len(lengths)) + ' segments'
  #simulate(lengths, args.output_prefix, args.max_segments, args.separate_next_segments_prefix, not args.no_separate_subtiles)
  print 'creating 1 week of speeds at hourly intervals for ' + str(len(lengths)) + ' segments'
  createSpeedTiles(lengths, args.output_prefix, args.max_segments, args.separate_next_segments_prefix, not args.no_separate_subtiles, segments)

  print 'done'

