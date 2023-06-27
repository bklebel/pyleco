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

import pytest
from jsonrpcobjects.objects import RequestObject, ErrorResponseObject

from pyleco.errors import NODE_UNKNOWN, NOT_SIGNED_IN, DUPLICATE_NAME, RECEIVER_UNKNOWN, generate_error_with_data
from pyleco.core import VERSION_B
from pyleco.core.message import Message
from pyleco.utils.coordinator_utils import FakeMultiSocket, FakeNode
from pyleco.test import FakeContext

from pyleco.coordinators.coordinator import Coordinator


@pytest.fixture
def coordinator():
    coordinator = Coordinator(namespace="N1", host="N1host", cleaning_interval=1e5,
                              context=FakeContext(),  # type: ignore
                              multi_socket=FakeMultiSocket()
                              )
    d = coordinator.directory
    d.add_component(b"send", b"321")
    d.add_component(b"rec", b"123")
    d.add_node_sender(FakeNode(), "N2host:12300", namespace=b"N2")
    d._nodes[b"N2"] = d._waiting_nodes.pop("N2host:12300")
    d._nodes[b"N2"].namespace = b"N2"
    d._waiting_nodes = {}
    d.add_node_receiver(b"n2", b"N2")
    n2 = coordinator.directory.get_node(b"N2")
    n2._messages_sent = []  # type: ignore # reset dealer sock._socket.
    n2.heartbeat = -1
    coordinator.sock._messages_sent = []  # type: ignore reset router sock._socket
    return coordinator


def fake_perf_counter():
    return 0.


@pytest.fixture()
def fake_counting(monkeypatch):
    # TODO adjust to pyleco
    monkeypatch.setattr("pyleco.utils.coordinator_utils.perf_counter", fake_perf_counter)


class Test_clean_addresses:
    def test_expired_component(self, coordinator: Coordinator, fake_counting):
        coordinator.directory.get_components()[b"send"].heartbeat = -3.5
        coordinator.clean_addresses(1)
        assert b"send" not in coordinator.directory.get_component_names()

    def test_expired_component_updates_directory(self, coordinator: Coordinator, fake_counting):
        coordinator.directory.get_components()[b"send"].heartbeat = -3.5
        coordinator.clean_addresses(1)
        assert coordinator.directory.get_nodes()[b"N2"]._messages_sent == [
            Message(
                receiver=b"N2.COORDINATOR", sender=b"N1.COORDINATOR",
                data=[{"id": 2, "method": "set_nodes", "params":
                       {"nodes": {"N1": "N1host:12300", "N2": "N2host:12300"}}, "jsonrpc": "2.0"},
                      {"id": 3, "method": "set_remote_components", "params":
                       {"components": ["rec"]}, "jsonrpc": "2.0"}],
            )
        ]

    def test_warn_component(self, coordinator: Coordinator, fake_counting):
        # TODO implement heartbeat request
        coordinator.directory.get_components()[b"send"].heartbeat = -1.5
        coordinator.clean_addresses(1)
        assert coordinator.sock._messages_sent == [(b"321", Message(
            b"N1.send", b"N1.COORDINATOR", data=RequestObject(id=0, method="pong")))]

    def test_active_Component_remains_in_directory(self, coordinator: Coordinator, fake_counting):
        coordinator.directory.get_components()[b"send"].heartbeat = -0.5
        coordinator.clean_addresses(1)
        assert coordinator.sock._messages_sent == []
        assert b"send" in coordinator.directory.get_components()

    def test_expired_Coordinator(self, coordinator: Coordinator, fake_counting):
        coordinator.directory.get_node_ids()[b"n2"].heartbeat = -3.5
        coordinator.clean_addresses(1)
        assert b"n2" not in coordinator.directory.get_node_ids()
        # further removal tests in :class:`Test_remove_coordinator`

    def test_warn_Coordinator(self, coordinator: Coordinator, fake_counting):
        coordinator.directory.get_node_ids()[b"n2"].heartbeat = -1.5
        coordinator.clean_addresses(1)
        assert coordinator.directory.get_node_ids()[b"n2"]._messages_sent == [
            Message(
                b'N2.COORDINATOR', b'N1.COORDINATOR',
                data=[
                    {"id": 2, "method": "set_nodes",
                     "params": {"nodes": {"N1": "N1host:12300", "N2": "N2host:12300"}},
                     "jsonrpc": "2.0"},
                    {"id": 3, "method": "set_remote_components",
                     "params": {"components": ["send", "rec"]},
                     "jsonrpc": "2.0"}]
            ),
            Message(b"N2.COORDINATOR", b"N1.COORDINATOR", data=RequestObject(id=0, method="pong")),
        ]

    def test_active_Coordinator_remains_in_directory(self, coordinator: Coordinator, fake_counting):
        coordinator.directory.get_node_ids()[b"n2"].heartbeat = -0.5
        coordinator.clean_addresses(1)
        assert b"n2" in coordinator.directory.get_node_ids()


def test_heartbeat_local(fake_counting, coordinator: Coordinator):
    coordinator.sock._messages_read = [[b"321", Message(b"COORDINATOR", b"send")]]
    coordinator.read_and_route()
    assert coordinator.directory.get_components()[b"send"].heartbeat == 0


@pytest.mark.parametrize("i, o", (
    ([b"321", VERSION_B, b"COORDINATOR", b"send", b";", b""], None),  # test heartbeat alone
    ([b"321", VERSION_B, b"rec", b"send", b";", b"1"],
     [b"123", VERSION_B, b"rec", b"send", b";", b"1"]),  # receiver known, sender known.
))
def test_routing_successful(coordinator: Coordinator, i, o):
    """Test whether some incoming message `i` is sent as `o`. Here: successful routing."""
    coordinator.sock._messages_read = [(i[0], Message.from_frames(*i[1:]))]
    coordinator.read_and_route()
    if o is None:
        assert coordinator.sock._messages_sent == []
    else:
        assert coordinator.sock._messages_sent == [(o[0], Message.from_frames(*o[1:]))]


@pytest.mark.parametrize("i, o", (
    # receiver unknown, return to sender:
    ([b"321", VERSION_B, b"x", b"send", b";", b""],
     [b"321", VERSION_B, b"send", b"N1.COORDINATOR", b";",
      ErrorResponseObject(id=None, error=generate_error_with_data(RECEIVER_UNKNOWN,
                                                               "x")).json().encode()]),
    # unknown receiver node:
    ([b"321", VERSION_B, b"N3.CB", b"N1.send", b";"],
     [b"321", VERSION_B, b"N1.send", b"N1.COORDINATOR", b";",
      ErrorResponseObject(id=None, error=generate_error_with_data(NODE_UNKNOWN,
                                                               "N3")).json().encode()]),
    # sender (without namespace) did not sign in:
    ([b"1", VERSION_B, b"rec", b"unknownSender", b"5;"],
     [b"1", VERSION_B, b"unknownSender", b"N1.COORDINATOR", b"5;",
      ErrorResponseObject(id=None, error=NOT_SIGNED_IN).json().encode()]),
    # sender (with given Namespace) did not sign in:
    ([b"1", VERSION_B, b"rec", b"N1.unknownSender", b"5;"],
     [b"1", VERSION_B, b"N1.unknownSender", b"N1.COORDINATOR", b"5;",
      ErrorResponseObject(id=None, error=NOT_SIGNED_IN).json().encode()]),
    # unknown sender with a rogue node name:
    ([b"1", VERSION_B, b"rec", b"N2.unknownSender", b"5;"],
     [b"1", VERSION_B, b"N2.unknownSender", b"N1.COORDINATOR", b"5;",
      ErrorResponseObject(id=None, error=NOT_SIGNED_IN).json().encode()]),
))
def test_routing_error_messages(coordinator: Coordinator, i, o):
    """Test whether some incoming message `i` is sent as `o`. Here: Error messages."""
    # TODO change to json
    coordinator.sock._messages_read = [(i[0], Message.from_frames(*i[1:]))]
    coordinator.read_and_route()
    if o is None:
        assert coordinator.sock._messages_sent == []
    else:
        assert coordinator.sock._messages_sent == [(o[0], Message.from_frames(*o[1:]))]


def test_remote_routing(coordinator: Coordinator):
    coordinator.sock._messages_read = [[b"321", Message(b"N2.CB", b"N1.send")]]
    coordinator.read_and_route()
    assert coordinator.directory.get_node(b"N2")._messages_sent == [
        Message(b"N2.CB", b"N1.send")]


@pytest.mark.parametrize("sender", (b"N2.CB", b"N2.COORDINATOR"))
def test_remote_heartbeat(coordinator: Coordinator, fake_counting, sender):
    coordinator.sock._messages_read = [[b"n2", Message(b"N3.CA", sender)]]
    assert coordinator.directory.get_node_ids()[b"n2"].heartbeat != 0
    coordinator.read_and_route()
    assert coordinator.directory.get_node_ids()[b"n2"].heartbeat == 0


class Test_handle_commands:
    class SpecialCoordinator(Coordinator):
        def handle_rpc_call(self, sender_identity: bytes, message: Message) -> None:
            self._rpc = sender_identity, message

    @pytest.fixture
    def coordinator_hc(self) -> Coordinator:
        return self.SpecialCoordinator(
            namespace="N1", host="N1host", cleaning_interval=1e5,
            context=FakeContext(),  # type: ignore
            multi_socket=FakeMultiSocket())

    def test_store_message(self, coordinator_hc: Coordinator):
        msg = Message(b"receiver", b"sender", header=b"header", data=b"data")
        coordinator_hc.handle_commands(b"identity", msg)
        assert coordinator_hc.current_message == msg

    def test_store_identity(self, coordinator_hc: Coordinator):
        msg = Message(b"receiver", b"sender", header=b"header", data=b"data")
        coordinator_hc.handle_commands(b"identity", msg)
        assert coordinator_hc.current_identity == b"identity"

    @pytest.mark.parametrize("identity, message", (
        (b"3", Message(b"", data={"jsonrpc": "2.0", "method": "some"})),
    ))
    def test_call_handle_rpc_call(self, coordinator_hc: Coordinator, identity, message):
        coordinator_hc.handle_commands(identity, message)
        assert coordinator_hc._rpc == (identity, message)

    def test_log_error_response(self, coordinator_hc: Coordinator):
        pass  # TODO

    def test_pass_at_null_result(self, coordinator_hc: Coordinator):
        coordinator_hc.handle_commands(b"",
                                       Message(b"", data={"jsonrpc": "2.0", "result": None}))
        assert not hasattr(coordinator_hc, "_rpc")
        # assert no error log entry.  TODO


class Test_sign_in:
    def test_signin(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'cb', Message(b"COORDINATOR", b"CB",
                                                           data=RequestObject(id=7,
                                                                              method="sign_in"),
                                                           conversation_id=b"7",
                                                           )]]
        # read_and_route needs to start at routing, to check that the messages passes the heartbeats
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == [
            (b"cb", Message(b"CB", b"N1.COORDINATOR",
                            conversation_id=b"7",
                            data={"id": 7, "result": None, "jsonrpc": "2.0"}))]

    def test_signin_sends_directory_update(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'cb', Message(b"COORDINATOR", b"CB",
                                                           data={"jsonrpc": "2.0",
                                                                 "method": "sign_in", "id": 7},
                                                           conversation_id=b"7",
                                                           )]]
        # read_and_route needs to start at routing, to check that the messages passes the heartbeats
        coordinator.read_and_route()
        assert coordinator.directory.get_node(b"N2")._messages_sent == [Message(
            b"N2.COORDINATOR", b"N1.COORDINATOR",
            data=[
                {"id": 2, "method": "set_nodes",
                 "params": {"nodes": {"N1": "N1host:12300", "N2": "N2host:12300"}},
                 "jsonrpc": "2.0"},
                {"id": 3, "method": "set_remote_components",
                 "params": {"components": ["send", "rec", "CB"]},
                 "jsonrpc": "2.0"}]
        )]

    def test_signin_rejected(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'cb', Message(b"COORDINATOR", b"send",
                                                           data={"id": 8, "method": "sign_in",
                                                                 "jsonrpc": "2.0"},
                                                           conversation_id=b"7",
                                                           message_id=b"1",
                                                           )]]
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == [(b"cb", Message(
            b"send", b"N1.COORDINATOR",
            conversation_id=b"7",
            data={"id": 8, "error": {"code": -32000, "message": "Server error",
                                     "data": "ValueError: The name is already taken."},
                  "jsonrpc": "2.0"}
        ))]

    def test_sign_in_fails_with_duplicate_name(self, coordinator: Coordinator):
        coordinator.current_message = Message(
            b"COORDINATOR", b"send",
            data={"id": 8, "method": "sign_in", "jsonrpc": "2.0"},
            conversation_id=b"7")
        coordinator.current_identity = b"cb"
        with pytest.raises(ValueError, match="The name is already taken."):
            coordinator.sign_in()


class Test_sign_out:
    def test_signout_clears_address(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'123', Message(
            b"N1.COORDINATOR", b"rec",
            data={"jsonrpc": "2.0", "method": "sign_out", "id": 10})]]
        coordinator.read_and_route()
        assert b"rec" not in coordinator.directory.get_components().keys()
        assert coordinator.sock._messages_sent == [
            (b"123", Message(b"rec", b"N1.COORDINATOR",
                             data={"id": 10, "result": None, "jsonrpc": "2.0"}))]

    def test_signout_clears_address_explicit_namespace(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'123', Message(
            b"N1.COORDINATOR", b"N1.rec",
            data={"jsonrpc": "2.0", "method": "sign_out", "id": 10})]]
        coordinator.read_and_route()
        assert b"rec" not in coordinator.directory.get_components().keys()
        assert coordinator.sock._messages_sent == [
            (b"123", Message(b"N1.rec", b"N1.COORDINATOR",
                             data={"id": 10, "result": None, "jsonrpc": "2.0"}))]

    def test_signout_sends_directory_update(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'123', Message(
            b"N1.COORDINATOR", b"rec",
            data={"jsonrpc": "2.0", "method": "sign_out", "id": 10})]]
        coordinator.read_and_route()
        assert coordinator.directory.get_node(b"N2")._messages_sent == [Message(
            b"N2.COORDINATOR", b"N1.COORDINATOR",
            data=[
                {"id": 2, "method": "set_nodes",
                 "params": {"nodes": {"N1": "N1host:12300", "N2": "N2host:12300"}},
                 "jsonrpc": "2.0"},
                {"id": 3, "method": "set_remote_components", "params": {"components": ["send"]},
                 "jsonrpc": "2.0"}]
                         )]

    def test_signout_requires_new_signin(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [[b'123', Message(
            b"N1.COORDINATOR", b"rec",
            data={"jsonrpc": "2.0", "method": "sign_out", "id": 10})]]
        coordinator.read_and_route()  # handle signout
        coordinator.sock._messages_sent = []
        coordinator.sock._messages_read = [[b'123', Message(
            b"N1.COORDINATOR", b"rec",
            data={"jsonrpc": "2.0", "result": None, "id": 11})]]
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == [(b"123", Message(
            b"rec", b"N1.COORDINATOR", data=ErrorResponseObject(id=None, error=NOT_SIGNED_IN)))]


class Test_coordinator_sign_in:
    def test_co_signin_unknown_coordinator_successful(self, coordinator: Coordinator):
        """Test that an unknown Coordinator may sign in."""
        coordinator.sock._messages_read = [
            [b'n3', Message(b"COORDINATOR", b"N3.COORDINATOR",
                            data={"jsonrpc": "2.0", "method": "coordinator_sign_in", "id": 15},
                            conversation_id=b"x")]]
        coordinator.read_and_route()
        assert b'n3' in coordinator.directory.get_node_ids().keys()
        assert coordinator.sock._messages_sent == [
            (b'n3', Message(b"COORDINATOR", b"N1.COORDINATOR",
                            conversation_id=b"x", data={"id": 15, "result": None,
                                                        "jsonrpc": "2.0"}))]

    def test_co_signin_known_coordinator_successful(self, fake_counting, coordinator: Coordinator):
        """Test that a Coordinator may sign in as a response to N1's sign in."""

        coordinator.directory.add_node_sender(FakeNode(), "N3host:12345", namespace=b"N3")
        coordinator.directory.get_nodes()[b"N3"] = coordinator.directory._waiting_nodes.pop(
            "N3host:12345")
        coordinator.directory.get_nodes()[b"N3"].namespace = b"N3"

        coordinator.sock._messages_read = [
            [b'n3', Message(b"COORDINATOR", b"N3.COORDINATOR",
                            conversation_id=b"x",
                            data={"jsonrpc": "2.0", "method": "coordinator_sign_in", "id": 15},)]]
        coordinator.read_and_route()
        assert b'n3' in coordinator.directory.get_node_ids().keys()
        assert coordinator.sock._messages_sent == [(b'n3', Message(
            b"COORDINATOR", b"N1.COORDINATOR", data={"id": 15, "result": None,
                                                     "jsonrpc": "2.0"}, conversation_id=b"x"))]

    def test_co_signin_rejected(self, coordinator: Coordinator):
        """Coordinator sign in rejected due to already connected Coordinator."""
        coordinator.sock._messages_read = [
            [b'n3', Message(b"COORDINATOR", b"N2.COORDINATOR",
                            data={"jsonrpc": "2.0", "method": "coordinator_sign_in", "id": 15},
                            conversation_id=b"x")]]
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == [(b"n3", Message(
            b"COORDINATOR", b"N1.COORDINATOR",
            data={"id": 15, "error": {"code": -32000, "message": "Server error",
                                      "data": "ValueError: Another Coordinator is known!"},
                  "jsonrpc": "2.0"},
            conversation_id=b"x"))]

    def test_coordinator_sign_in_fails_at_duplicate_name(self, coordinator: Coordinator):
        coordinator.current_message = Message(
            b"COORDINATOR", b"N2.COORDINATOR",
            data={"jsonrpc": "2.0", "method": "coordinator_sign_in", "id": 15},
            conversation_id=b"x")
        coordinator.current_identity = b"n3"
        with pytest.raises(ValueError, match="Another Coordinator is known!"):
            coordinator.coordinator_sign_in()

    def test_co_signin_of_self_rejected(self, coordinator: Coordinator):
        """Coordinator sign in rejected because it is the same coordinator."""
        coordinator.sock._messages_read = [
            [b'n3', Message(b"COORDINATOR", b"N1.COORDINATOR",
                            conversation_id=b"x", data={"jsonrpc": "2.0",
                                                        "method": "coordinator_sign_in",
                                                        "id": 15})]]
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == [
            (b'n3', Message(b"N1.COORDINATOR", b"N1.COORDINATOR", conversation_id=b"x",
             data=ErrorResponseObject(id=None, error=NOT_SIGNED_IN)))]


class Test_coordinator_sign_out:
    def test_co_signout_successful(self, coordinator: Coordinator):
        coordinator.sock._messages_read = [
            [b'n2', Message(b"COORDINATOR", b"N2.COORDINATOR",
                            conversation_id=b"x",
                            data={"id": 10, "method": "coordinator_sign_out", "jsonrpc": "2.0"})]]
        node = coordinator.directory.get_node(b"N2")
        coordinator.read_and_route()
        assert b"n2" not in coordinator.directory.get_node_ids()
        assert node._messages_sent == [Message(
            b"N2.COORDINATOR", b"N1.COORDINATOR", conversation_id=b"x",
            data={"id": 100, "method": "coordinator_sign_out", "jsonrpc": "2.0"})]

    @pytest.mark.xfail(True, reason="Not yet defined.")
    def test_co_signout_rejected_due_to_different_identity(self, coordinator: Coordinator):
        """TODO TBD how to handle it"""
        coordinator.set_log_level(10)
        coordinator.sock._messages_read = [
            [b'n4', Message(
                receiver=b"COORDINATOR", sender=b"N2.COORDINATOR", conversation_id=b"x",
                data={"id": 10, "method": "coordinator_sign_out", "jsonrpc": "2.0"})]]
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == [
            (b"n4", Message(
                receiver=b"N2.COORDINATOR", sender=b"N1.COORDINATOR", conversation_id=b"x",
                data=ErrorResponseObject(id=None, error=NOT_SIGNED_IN)))]

    def test_co_signout_of_not_signed_in_coordinator(self, coordinator: Coordinator):
        """TODO TBD whether to reject or to ignore."""
        coordinator.sock._messages_read = [
            (b"n4", Message(b"COORDINATOR", b"N4.COORDINATOR",
                            data={"id": 10, "method": "coordinator_sign_out", "jsonrpc": "2.0"}))]
        coordinator.read_and_route()
        assert coordinator.sock._messages_sent == []


class Test_shutdown:
    def test_shutdown_coordinator(self, coordinator: Coordinator):
        n2 = coordinator.directory.get_node(b"N2")
        coordinator.shutdown()
        # Assert sign out messages
        assert n2._messages_sent == [
            Message(b"N2.COORDINATOR", b"N1.COORDINATOR",
                    data={"id": 2, "method": "coordinator_sign_out", "jsonrpc": "2.0"})]


class Test_compose_local_directory:
    def test_compose_local_directory(self, coordinator: Coordinator):
        data = coordinator.compose_local_directory()
        assert data == {
                "directory": ["send", "rec"],
                "nodes": {"N1": "N1host:12300", "N2": "N2host:12300"}
            }


class Test_compose_global_directory:
    def test_compose_global_directory(self, coordinator: Coordinator):
        coordinator.global_directory[b"N5"] = ["some", "coordinator"]
        data = coordinator.compose_global_directory()
        assert data == {
                 "N5": ["some", "coordinator"],
                 "nodes": {"N1": "N1host:12300", "N2": "N2host:12300"},
                 "N1": ["send", "rec"]}


class Test_set_nodes:
    def test_set_nodes(self, coordinator: Coordinator, fake_counting):
        coordinator.set_nodes({"N1": "N1host:12300", "N2": "wrong_host:-7", "N3": "N3host:12300"})
        assert coordinator.directory.get_node(b"N2").address == "N2host:12300"  # not changed
        assert "N3host:12300" in coordinator.directory._waiting_nodes.keys()  # newly created


class Test_set_remote_components:
    def test_set(self, coordinator: Coordinator):
        coordinator.current_message = Message(b"", sender="N2.COORDINATOR")
        coordinator.set_remote_components(["send", "rec"])
        assert coordinator.global_directory == {b"N2": ["send", "rec"]}
