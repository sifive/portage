# Copyright 1999-2012 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2

import array
import errno
import logging
import os
import time

from portage.util import writemsg_level
from _emerge.AsynchronousTask import AsynchronousTask
from _emerge.PollConstants import PollConstants
class AbstractPollTask(AsynchronousTask):

	__slots__ = ("scheduler",) + \
		("_registered",)

	_bufsize = 4096
	_exceptional_events = PollConstants.POLLERR | PollConstants.POLLNVAL
	_registered_events = PollConstants.POLLIN | PollConstants.POLLHUP | \
		_exceptional_events

	def isAlive(self):
		return bool(self._registered)

	def _read_array(self, f, event):
		"""
		NOTE: array.fromfile() is used here only for testing purposes,
		because it has bugs in all known versions of Python (including
		Python 2.7 and Python 3.2). See PipeReaderArrayTestCase.

		| POLLIN | RETURN
		| BIT    | VALUE
		| ---------------------------------------------------
		| 1      | Read self._bufsize into an instance of
		|        | array.array('B') and return it, handling
		|        | EOFError and IOError. An empty array
		|        | indicates EOF.
		| ---------------------------------------------------
		| 0      | None
		"""
		buf = None
		if event & PollConstants.POLLIN:
			buf = array.array('B')
			try:
				buf.fromfile(f, self._bufsize)
			except EOFError:
				pass
			except TypeError:
				# Python 3.2:
				# TypeError: read() didn't return bytes
				pass
			except IOError as e:
				# EIO happens with pty on Linux after the
				# slave end of the pty has been closed.
				if e.errno == errno.EIO:
					# EOF: return empty string of bytes
					pass
				elif e.errno == errno.EAGAIN:
					# EAGAIN: return None
					buf = None
				else:
					raise

		if buf is not None:
			try:
				# Python >=3.2
				buf = buf.tobytes()
			except AttributeError:
				buf = buf.tostring()

		return buf

	def _read_buf(self, fd, event):
		"""
		| POLLIN | RETURN
		| BIT    | VALUE
		| ---------------------------------------------------
		| 1      | Read self._bufsize into a string of bytes,
		|        | handling EAGAIN and EIO. An empty string
		|        | of bytes indicates EOF.
		| ---------------------------------------------------
		| 0      | None
		"""
		# NOTE: array.fromfile() is no longer used here because it has
		# bugs in all known versions of Python (including Python 2.7
		# and Python 3.2).
		buf = None
		if event & PollConstants.POLLIN:
			try:
				buf = os.read(fd, self._bufsize)
			except OSError as e:
				# EIO happens with pty on Linux after the
				# slave end of the pty has been closed.
				if e.errno == errno.EIO:
					# EOF: return empty string of bytes
					buf = b''
				elif e.errno == errno.EAGAIN:
					# EAGAIN: return None
					buf = None
				else:
					raise

		return buf

	def _unregister(self):
		raise NotImplementedError(self)

	def _log_poll_exception(self, event):
		writemsg_level(
			"!!! %s received strange poll event: %s\n" % \
			(self.__class__.__name__, event,),
			level=logging.ERROR, noiselevel=-1)

	def _unregister_if_appropriate(self, event):
		if self._registered:
			if event & self._exceptional_events:
				self._log_poll_exception(event)
				self._unregister()
				self.cancel()
			elif event & PollConstants.POLLHUP:
				self._unregister()
				self.wait()

	def _wait_loop(self, timeout=None):

		if timeout is None:
			while self._registered:
				self.scheduler.iteration()
			return

		remaining_timeout = timeout
		start_time = time.time()
		while self._registered:
			self.scheduler.iteration()
			elapsed_time = time.time() - start_time
			if elapsed_time < 0:
				# The system clock has changed such that start_time
				# is now in the future, so just assume that the
				# timeout has already elapsed.
				break
			remaining_timeout = timeout - 1000 * elapsed_time
			if remaining_timeout <= 0:
				break
