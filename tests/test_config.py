# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Eduardo Correia
import os

import pytest

from netsim import config


def test_instance_lock_is_exclusive(tmp_path):
    lock = tmp_path / "netsim.lock"
    fd = config.acquire_instance_lock(lock)
    try:
        with pytest.raises(config.InstanceRunning) as e:
            config.acquire_instance_lock(lock)  # a separate open file: conflicts like another process
        assert e.value.pid == str(os.getpid())
    finally:
        os.close(fd)
    os.close(config.acquire_instance_lock(lock))  # free again after release


def test_lock_does_not_depend_on_run_dir(monkeypatch):
    monkeypatch.setenv("NETSIM_RUN_DIR", "/tmp/elsewhere")
    assert "NETSIM_RUN_DIR" not in str(config.LOCK_PATH) and config.LOCK_PATH.name == "netsim.lock"
    assert config.RUN_DIR not in config.LOCK_PATH.parents
