# Copyright 2014 Confluent Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .test import KafkaTest
from ducktape.services.service import Service
from muckrake.services.performance import ProducerPerformanceService, ConsumerPerformanceService, EndToEndLatencyService


class KafkaBenchmark(KafkaTest):
    '''A benchmark of Kafka producer/consumer performance. This replicates the test
    run here:
    https://engineering.linkedin.com/kafka/benchmarking-apache-kafka-2-million-writes-second-three-cheap-machines
    '''
    def __init__(self, test_context):
        super(KafkaBenchmark, self).__init__(test_context, num_zk=1, num_brokers=3, topics={
            'test-rep-one' : { 'partitions': 6, 'replication-factor': 1 },
            'test-rep-three' : { 'partitions': 6, 'replication-factor': 3 }
        })

        if True:
            # Works on both aws and local
            self.msgs = 1000000
            self.msgs_default = 1000000
        else:
            # Can use locally on Vagrant VMs, but may use too much memory for aws
            self.msgs = 50000000
            self.msgs_default = 50000000

        self.msgs_large = 10000000
        self.msg_size_default = 100
        self.batch_size = 8*1024
        self.buffer_memory = 64*1024*1024
        self.msg_sizes = [10, 100, 1000, 10000, 100000]
        self.target_data_size = 128*1024*1024
        self.target_data_size_gb = self.target_data_size/float(1024*1024*1024)

    def test_single_producer_no_replication(self):
        self.logger.info("BENCHMARK: Single producer, no replication")
        self.perf = ProducerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-one", num_records=self.msgs_default, record_size=self.msg_size_default, throughput=-1,
            settings={'acks':1, 'batch.size':self.batch_size, 'buffer.memory':self.buffer_memory}
        )
        self.perf.run()
        self.logger.info("Single producer, no replication: %s", throughput(self.perf))

    def test_single_producer_replication(self):
        self.logger.info("BENCHMARK: Single producer, async 3x replication")
        self.perf = ProducerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, record_size=self.msg_size_default, throughput=-1,
            settings={'acks':1, 'batch.size':self.batch_size, 'buffer.memory':self.buffer_memory}
        )
        self.perf.run()
        self.logger.info("Single producer, async 3x replication: %s" % throughput(self.perf))

    def test_single_producer_sync(self):
        self.logger.info("BENCHMARK: Single producer, sync 3x replication")
        self.perf = ProducerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, record_size=self.msg_size_default, throughput=-1,
            settings={'acks':-1, 'batch.size':self.batch_size, 'buffer.memory':self.buffer_memory}
        )
        self.perf.run()
        self.logger.info("Single producer, sync 3x replication: %s" % throughput(self.perf))

    def test_three_producers_async(self):
        self.logger.info("BENCHMARK: Three producers, async 3x replication")
        self.perf = ProducerPerformanceService(
            self.test_context, 3, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, record_size=self.msg_size_default, throughput=-1,
            settings={'acks':1, 'batch.size':self.batch_size, 'buffer.memory':self.buffer_memory}
        )
        self.perf.run()
        self.logger.info("Three producers, async 3x replication: %s" % throughput(self.perf))

    def test_multiple_message_size(self):
        # TODO this would be a great place to use parametrization
        self.perfs = {}
        for msg_size in self.msg_sizes:
            self.logger.info("BENCHMARK: Message size %d (%f GB total, single producer, async 3x replication)", msg_size, self.target_data_size_gb)
            # Always generate the same total amount of data
            nrecords = int(self.target_data_size / msg_size)
            self.perfs["perf-" + str(msg_size)] = ProducerPerformanceService(
                self.test_context, 1, self.kafka,
                topic="test-rep-three", num_records=nrecords, record_size=msg_size, throughput=-1,
                settings={'acks': 1, 'batch.size': self.batch_size, 'buffer.memory': self.buffer_memory}
            )
        self.msg_size_perf = {}

        for msg_size in self.msg_sizes:
            perf = self.perfs["perf-" + str(msg_size)]
            perf.run()
            self.msg_size_perf[msg_size] = perf

        summary = ["Message size:"]
        for msg_size in self.msg_sizes:
            summary.append(" %d: %s" % (msg_size, throughput(self.msg_size_perf[msg_size])))
        self.logger.info("\n".join(summary))

    def test_long_term_throughput(self):
        self.logger.info("BENCHMARK: Long production")
        self.perf = ProducerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=self.msgs_large, record_size=self.msg_size_default, throughput=-1,
            settings={'acks':1, 'batch.size':self.batch_size, 'buffer.memory':self.buffer_memory},
            intermediate_stats=True
        )
        self.perf.run()

        summary = ["Throughput over long run, data > memory:"]

        # FIXME we should be generating a graph too
        # Try to break it into 5 blocks, but fall back to a smaller number if
        # there aren't even 5 elements
        block_size = max(len(self.perf.stats[0]) / 5, 1)
        nblocks = len(self.perf.stats[0]) / block_size
        for i in range(nblocks):
            subset = self.perf.stats[0][i*block_size:min((i+1)*block_size, len(self.perf.stats[0]))]
            if len(subset) == 0:
                summary.append(" Time block %d: (empty)" % i)
            else:
                summary.append(" Time block %d: %f rec/sec (%f MB/s)" % (i,
                                 sum([stat['records_per_sec'] for stat in subset])/float(len(subset)),
                                 sum([stat['mbps'] for stat in subset])/float(len(subset))))

        self.logger.info("\n".join(summary))

    def test_end_to_end_latency(self):
        self.logger.info("BENCHMARK: End to end latency")
        self.perf = EndToEndLatencyService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=10000
        )
        self.perf.run()

        self.logger.info("End-to-end latency: median %f ms, 99%% %f ms, 99.9%% %f ms" % \
               (self.perf.results[0]['latency_50th_ms'],
                self.perf.results[0]['latency_99th_ms'],
                self.perf.results[0]['latency_999th_ms']))

    def test_producer_and_consumer(self):
        self.logger.info("BENCHMARK: Producer + Consumer")
        self.producer = ProducerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, record_size=self.msg_size_default, throughput=-1,
            settings={'acks':1, 'batch.size':self.batch_size, 'buffer.memory':self.buffer_memory}
        )

        self.consumer = ConsumerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, throughput=-1, threads=1
        )

        Service.run_parallel(self.producer, self.consumer)

        summary = [
            "Producer + consumer:",
            " Producer: %s" % throughput(self.producer),
            " Consumer: %s" % throughput(self.consumer)]
        self.logger.info("\n".join(summary))

    def test_single_consumer(self):
        # All consumer tests use the messages from the first benchmark, so
        # they'll get messages of the default message size
        self.logger.info("BENCHMARK: Single consumer")
        self.perf = ConsumerPerformanceService(
            self.test_context, 1, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, throughput=-1, threads=1
        )
        self.perf.run()
        self.logger.info("Single consumer: %s" % throughput(self.perf))

    def test_three_consumers(self):
        self.logger.info("BENCHMARK: Three consumers")
        self.perf = ConsumerPerformanceService(
            self.test_context, 3, self.kafka,
            topic="test-rep-three", num_records=self.msgs_default, throughput=-1, threads=1
        )
        self.perf.run()
        self.logger.info("Three consumers: %s", throughput(self.perf))

def throughput(perf):
    """Helper method for computing throughput after running a performance service."""
    aggregate_rate = sum([r['records_per_sec'] for r in perf.results])
    aggregate_mbps = sum([r['mbps'] for r in perf.results])
    return "%f rec/sec (%f MB/s)" % (aggregate_rate, aggregate_mbps)







