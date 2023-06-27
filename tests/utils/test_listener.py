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

from pyleco.core import VERSION_B

from pyleco.core.message import Message
from pyleco.test import FakeContext

from pyleco.utils.listener import BaseListener


@pytest.fixture
def listener() -> BaseListener:
    listener = BaseListener(name="test", context=FakeContext())
    listener.node = "N1"
    listener.full_name = "N1.test"
    return listener


cid = b"7"  # conversation_id
# the result
msg = Message(b"r", b"s", conversation_id=b"7", message_id=b"1")
msg_list = ("r", "s", b"7", b"", None)
# some different message
other = Message(b"r", b"s", conversation_id=b"9", message_id=b"1")


def test_send(listener: BaseListener):
    listener._send("N2.CB", conversation_id=b"rec", message_id=b"sen", data=[["TEST"]])
    assert listener.socket._s == [[VERSION_B, b"N2.CB", b"N1.test", b"rec;sen", b'[["TEST"]]']]


class Test_check_message_in_buffer:
    def test_in_first_place(self, listener: BaseListener):
        listener._buffer = [msg]
        assert listener._check_message_in_buffer(cid) == msg
        assert listener._buffer == []

    def test_no_message(self, listener: BaseListener):
        listener._buffer = [other]
        assert listener._check_message_in_buffer(cid) is None
        assert listener._buffer == [other]

    def test_msg_somewhere_in_buffer(self, listener: BaseListener):
        o2 = Message(b"r", b"s", conversation_id=b"9", message_id=b"7")
        listener._buffer = [other, msg, o2]
        assert listener._check_message_in_buffer(cid) == msg
        assert listener._buffer == [other, o2]


@pytest.mark.parametrize("buffer", ([msg], [msg, other], [other, msg]))
def test_read_answer_success(listener: BaseListener, buffer):
    listener._buffer = buffer
    assert listener.read_answer(cid) == msg_list


@pytest.mark.parametrize("buffer", ([msg], [msg, other], [other, msg]))
def test_read_answer_as_message_success(listener: BaseListener, buffer):
    listener._buffer = buffer
    assert listener.read_answer_as_message(cid) == msg


@pytest.mark.parametrize("buffer", ([], [other]))
def test_read_answer_fail(listener: BaseListener, buffer):
    listener._buffer = buffer
    with pytest.raises(TimeoutError):
        listener.read_answer(cid)


class Test_ask:
    msg_outbound = {'receiver': "outbound", 'conversation_id': b"7"}

    @pytest.fixture
    def listener_asked(self, listener: BaseListener) -> BaseListener:
        listener._event.set()
        listener._buffer = [msg]
        listener.ask(**self.msg_outbound)
        return listener

    def test_event_cleared(self, listener_asked: BaseListener):
        assert listener_asked._event.is_set() is False

    def test_cid_added(self, listener_asked: BaseListener):
        assert listener_asked.cids[-1] == b"7"

    def test_message_sent(self, listener_asked: BaseListener):
        assert listener_asked.pipe.socket._s == [[b"SND", *Message(
            sender="N1.test", **self.msg_outbound).get_frames_list()]]
