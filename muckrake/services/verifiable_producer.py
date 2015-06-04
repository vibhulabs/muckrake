# Copyright 2015 Confluent Inc.
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

from muckrake.services.background_thread import BackgroundThreadService

import json
from Queue import Queue


class VerifiableProducer(BackgroundThreadService):

    logs = {
        "producer_log": {
            "path": "/mnt/producer.log",
            "collect_default": True}
    }

    def __init__(self, context, num_nodes, kafka, topic, num_messages=-1, throughput=100000):
        super(VerifiableProducer, self).__init__(context, num_nodes)

        self.kafka = kafka
        self.topic = topic
        self.max_messages = num_messages
        self.throughput = throughput

        self.acked_values = []
        self.not_acked_data = []
        self.not_acked_values = []

    def _worker(self, idx, node):
        cmd = self.start_cmd
        self.logger.debug("Verbose producer %d command: %s" % (idx, cmd))

        for line in node.account.ssh_capture(cmd):
            line = line.strip()

            data = self.try_parse_json(line)
            if data is not None:

                self.logger.debug("VerifiableProducer: " + str(data))

                try:
                    self.lock.acquire()
                    if data["name"] == "producer_send_error":
                        data["node"] = idx
                        self.not_acked_data.append(data)
                        self.not_acked_values.append(int(data["value"]))

                    elif data["name"] == "producer_send_success":
                        self.acked_values.append(int(data["value"]))
                finally:
                    self.lock.release()

    @property
    def start_cmd(self):
        cmd = "/opt/kafka/bin/kafka-run-class.sh org.apache.kafka.clients.tools.VerifiableProducer" \
              " --topic %s --broker-list %s" % (self.topic, self.kafka.bootstrap_servers())
        if self.max_messages > 0:
            cmd += " --max-messages %s" % str(self.max_messages)
        if self.throughput> 0:
            cmd += " --throughput %s" % str(self.throughput)

        cmd += " 2>> /mnt/producer.log | tee -a /mnt/producer.log &"
        return cmd

    @property
    def acked(self):
        try:
            self.lock.acquire()
            return self.acked_values
        finally:
            self.lock.release()

    @property
    def not_acked(self):
        try:
            self.lock.acquire()
            return self.not_acked_values
        finally:
            self.lock.release()

    @property
    def num_acked(self):
        try:
            self.lock.acquire()
            return len(self.acked_values)
        finally:
            self.lock.release()

    @property
    def num_not_acked(self):
        try:
            self.lock.acquire()
            return len(self.not_acked_values)
        finally:
            self.lock.release()

    def stop_node(self, node):
        node.account.kill_process("VerifiableProducer")

    def clean_node(self, node):
        node.account.ssh("rm -rf /mnt/producer.log")

    def try_parse_json(self, string):
        """Try to parse a string as json. Return None if not parseable."""
        try:
            record = json.loads(string)
            return record
        except ValueError:
            self.logger.debug("Could not parse as json: %s" % str(string))
            return None
