import os
import platform
from glob import glob
from time import sleep
import logging
import subprocess
import shutil
from typing import Optional, List

import aw_core

logger = logging.getLogger(__name__)

_module_dir = os.path.dirname(os.path.realpath(__file__))
_parent_dir = os.path.abspath(os.path.join(_module_dir, os.pardir))
_search_paths = [_module_dir, _parent_dir]


def _locate_bundled_executable(name: str) -> Optional[str]:
    """Returns the path to the module executable if it exists in the bundle, else None."""
    _exec_paths = [os.path.join(path, name) for path in _search_paths]

    # Look for it in the installation path
    for exec_path in _exec_paths:
        if os.path.isfile(exec_path):
            # logger.debug("Found executable for {} in: {}".format(name, exec_path))
            return exec_path


def _is_system_module(name) -> bool:
    """Checks if a module with a particular name exists in PATH"""
    return shutil.which(name) is not None


def _locate_executable(name: str) -> Optional[str]:
    """
    Will return the path to the executable if bundled,
    otherwise returns the name if it is available in PATH.

    Used when calling Popen.
    """
    exec_path = _locate_bundled_executable(name)
    if exec_path is not None:  # Check if it exists in bundle
        return exec_path
    elif _is_system_module(name):  # Check if it's in PATH
        return name
    else:
        logger.warning("Could not find module '{}' in installation directory or PATH".format(name))
        return None


def _discover_modules_bundled() -> List[str]:
    # Look for modules in source dir and parent dir
    modules = []
    for path in _search_paths:
        matches = glob(os.path.join(path, "aw-*"))
        for match in matches:
            if os.path.isfile(match) and os.access(match, os.X_OK):
                name = os.path.basename(match)
                modules.append(name)
            else:
                logger.warning("Found matching file but was not executable: {}".format(path))

    logger.info("Found bundled modules: {}".format(set(modules)))
    return modules


def _discover_modules_system() -> List[str]:
    search_paths = os.environ["PATH"].split(":")
    modules = []
    for path in search_paths:
        files = os.listdir(path)
        for filename in files:
            if "aw-" in filename:
                modules.append(filename)

    logger.info("Found system modules: {}".format(set(modules)))
    return modules


class Module:
    def __init__(self, name: str, testing: bool = False) -> None:
        self.name = name
        self.started = False
        self.testing = testing
        self._process = None  # type: Optional[subprocess.Popen]
        self._last_process = None  # type: Optional[subprocess.Popen]

    def start(self) -> None:
        logger.info("Starting module {}".format(self.name))

        # Create a process group, become its leader
        # TODO: This shouldn't go here
        if platform.system() != "Windows":
            os.setpgrp()

        exec_path = _locate_executable(self.name)
        if exec_path is None:
            return
        else:
            exec_cmd = [exec_path]
            if self.testing:
                exec_cmd.append("--testing")
            # logger.debug("Running: {}".format(exec_cmd))

            # There is a very good reason stdout and stderr is not PIPE here
            # See: https://github.com/ActivityWatch/aw-server/issues/27
            self._process = subprocess.Popen(exec_cmd, universal_newlines=True)

            # Should be True if module is supposed to be running, else False
            self.started = True

    def stop(self) -> None:
        """
        Stops a module, and waits until it terminates.
        """
        # TODO: What if a module doesn't stop? Add timeout to p.wait() and then do a p.kill() if timeout is hit
        if not self.started:
            logger.warning("Tried to stop module {}, but it hasn't been started".format(self.name))
            return
        elif not self.is_alive():
            logger.warning("Tried to stop module {}, but it wasn't running".format(self.name))
        else:
            logger.debug("Stopping module {}".format(self.name))
            self._process.terminate()
            logger.debug("Waiting for module {} to shut down".format(self.name))
            self._process.wait()
            logger.info("Stopped module {}".format(self.name))

        assert not self.is_alive()
        self._last_process = self._process
        self._process = None
        self.started = False

    def toggle(self) -> None:
        if self.started:
            self.stop()
        else:
            self.start()

    def is_alive(self) -> bool:
        if self._process is None:
            return False

        self._process.poll()
        # If returncode is none after p.poll(), module is still running
        return True if self._process.returncode is None else False

    def read_log(self) -> str:
        """Useful if you want to retrieve the logs of a module"""
        log_path = aw_core.log.get_latest_log_file(self.name, self.testing)
        if log_path:
            with open(log_path) as f:
                return f.read()
        else:
            return "No log file found"


class Manager:
    def __init__(self, testing: bool = False) -> None:
        self.testing = testing
        self.modules = {}  # type: Dict[str, Module]

        self.discover_modules()

    def discover_modules(self):
        # These should always be bundled with aw-qt
        found_modules = {
            "aw-server",
            "aw-watcher-afk",
            "aw-watcher-window"
        }
        found_modules |= set(_discover_modules_bundled())
        found_modules |= set(_discover_modules_system())
        found_modules ^= {"aw-qt"}  # Exclude self

        for m_name in found_modules:
            if m_name not in self.modules:
                self.modules[m_name] = Module(m_name, testing=self.testing)

    def get_unexpected_stops(self):
        return list(filter(lambda x: x.started and not x.is_alive(), self.modules.values()))

    def start(self, module_name):
        if module_name in self.modules.keys():
            self.modules[module_name].start()
        else:
            logger.error("Unable to start module '{}': No such module".format(module_name))

    def autostart(self, autostart_modules):
        # Always start aw-server first
        if "aw-server" in autostart_modules:
            self.start("aw-server")

        autostart_modules = set(autostart_modules) - {"aw-server"}
        for module_name in autostart_modules:
            self.start(module_name)

    def stop_all(self):
        for module in filter(lambda m: m.is_alive(), self.modules.values()):
            module.stop()


if __name__ == "__main__":
    manager = Manager()
    for module in manager.modules.values():
        module.start()
        sleep(2)
        assert module.is_alive()
        module.stop()
