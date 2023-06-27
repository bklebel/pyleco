#
# This file is part of the PyLECO package.
#
# Copyright (c) 2023-2023 PyLECO Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import logging
from socket import gethostname
from time import sleep
import threading

import pytest

from pyleco.core.message import Message
from pyleco.utils.listener import BaseListener
from pyleco.utils.communicator import SimpleCommunicator

from pyleco.coordinators.coordinator import Coordinator


hostname = gethostname()
testlevel = 30
# pytest.skip("Takes too long.", allow_module_level=True)


def start_coordinator(namespace: str, port: int, coordinators=None, **kwargs):
    with Coordinator(namespace=namespace, port=port, **kwargs) as coordinator:
        coordinator.routing(coordinators=coordinators)
        print("stopping!")


@pytest.fixture(scope="module")
def leco():
    """A leco setup."""
    glog = logging.getLogger()
    glog.setLevel(logging.DEBUG)
    glog.addHandler(logging.StreamHandler())
    log = logging.getLogger("test")
    threads = []
    threads.append(threading.Thread(target=start_coordinator,
                                    kwargs=dict(namespace="N1", port=60001)))
    threads.append(threading.Thread(target=start_coordinator,
                                    kwargs=dict(namespace="N2", port=60002)))
    threads.append(threading.Thread(target=start_coordinator,
                                    kwargs=dict(namespace="N3", port=60003)))
    for thread in threads:
        thread.daemon = True
        thread.start()
    listener = BaseListener(name="Controller", port=60001)
    listener.start_listen()
    sleep(1)
    yield listener
    log.info("Tearing down")
    for thread in threads:
        thread.join(0.5)
    listener.stop_listen()


@pytest.mark.skipif(testlevel < 0, reason="reduce load")
def test_startup(leco: BaseListener):
    directory = leco.ask_rpc(b"COORDINATOR", "compose_local_directory")
    assert directory == {"directory": ["Controller"],
                         "nodes": {"N1": f"{hostname}:60001"}}


@pytest.mark.skipif(testlevel < 1, reason="reduce load")
def test_connect_N1_to_N2(leco: BaseListener):
    response = leco.ask_rpc("COORDINATOR", method="set_nodes", nodes={"N2": "localhost:60002"})
    assert response is None
    sleep(0.5)  # time for coordinators to talk
    nodes = leco.ask_rpc(receiver="COORDINATOR", method="compose_local_directory").get("nodes")
    assert nodes == {"N1": f"{hostname}:60001", "N2": "localhost:60002"}


@pytest.mark.skipif(testlevel < 2, reason="reduce load")
def test_Component_to_Component_via_1_Coordinator(leco: BaseListener):
    c = SimpleCommunicator(name="whatever", port=60001)
    assert c.ask("N1.Controller", data={"id": 1, "method": "pong", "jsonrpc": "2.0"}) == Message(
        b'N1.whatever', b'N1.Controller', data={"id": 1, "result": None, "jsonrpc": "2.0"}
    )


@pytest.mark.skipif(testlevel < 2, reason="reduce load")
def test_Component_to_Component_via_2_Coordinators(leco: BaseListener):
    with SimpleCommunicator(name="whatever", port=60002) as c:
        response = c.ask("N1.Controller", data={"id": 1, "method": "pong", "jsonrpc": "2.0"})
        assert response == Message(
            b'N2.whatever', b'N1.Controller', data={"id": 1, "result": None, "jsonrpc": "2.0"})


@pytest.mark.skipif(testlevel < 3, reason="reduce load")
def test_connect_N3_to_N2(leco: BaseListener):
    c = SimpleCommunicator(name="whatever", port=60003)
    c.sign_in()
    c.ask_rpc(b"COORDINATOR", "set_nodes", nodes={"N2": "localhost:60002"})

    sleep(0.5)  # time for coordinators to talk
    nodes = leco.ask_rpc(receiver="COORDINATOR", method="compose_local_directory").get("nodes")
    assert nodes == {"N1": f"{hostname}:60001", "N2": "localhost:60002",
                     "N3": f"{hostname}:60003"}


@pytest.mark.skipif(testlevel < 4, reason="reduce load")
def test_shutdown_N3(leco: BaseListener):
    c = SimpleCommunicator(name="whatever", port=60003)
    c.sign_in()
    c.ask(receiver="N3.COORDINATOR", data={"id": 3, "method": "shutdown", "jsonrpc": "2.0"})

    sleep(0.5)  # time for coordinators to talk
    nodes = leco.ask_rpc(receiver="COORDINATOR", method="compose_local_directory").get("nodes")
    assert nodes == {"N1": f"{hostname}:60001", "N2": "localhost:60002"}
