FROM ubuntu:16.04
MAINTAINER Grant Heffernan <grant@mapzen.com>

# env
ENV DEBIAN_FRONTEND noninteractive

ENV STORE_BIND_ADDR ${STORE_BIND_ADDR:-"0.0.0.0"}
ENV STORE_LISTEN_PORT ${STORE_LISTEN_PORT:-"8003"}

# install dependencies
RUN apt-get update && apt-get install -y python python-psycopg2

# install code
ADD ./py /datastore

# cleanup
RUN apt-get clean && \
      rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

EXPOSE ${STORE_LISTEN_PORT}

# start the datastore service
CMD python -u /datastore/datastore_service.py ${STORE_BIND_ADDR}:${STORE_LISTEN_PORT}
