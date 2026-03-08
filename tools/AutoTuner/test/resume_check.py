#############################################################################
##
## Copyright (c) 2024, Precision Innovations Inc.
## All rights reserved.
##
## BSD 3-Clause License
##
## Redistribution and use in source and binary forms, with or without
## modification, are permitted provided that the following conditions are met:
##
## * Redistributions of source code must retain the above copyright notice, this
##   list of conditions and the following disclaimer.
##
## * Redistributions in binary form must reproduce the above copyright notice,
##   this list of conditions and the following disclaimer in the documentation
##   and/or other materials provided with the distribution.
##
## * Neither the name of the copyright holder nor the names of its
##   contributors may be used to endorse or promote products derived from
##   this software without specific prior written permission.
##
## THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
## AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
## IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
## ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
## LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
## CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
## SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
## INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
## CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
## ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
## POSSIBILITY OF SUCH DAMAGE.
###############################################################################

import unittest
import subprocess
import os
import time
from .autotuner_test_utils import AutoTunerTestUtils, accepted_rc

from contextlib import contextmanager
from ray.tune import ExperimentAnalysis

cur_dir = os.path.dirname(os.path.abspath(__file__))
orfs_flow_dir = os.path.join(cur_dir, "../../../flow")

# Maximum time (seconds) to wait for trials to start producing results.
POLL_TIMEOUT = 300
# Interval (seconds) between status polls.
POLL_INTERVAL = 15
# Maximum time (seconds) to wait for Ray cluster to shut down.
RAY_SHUTDOWN_TIMEOUT = 120


@contextmanager
def managed_process(*args, **kwargs):
    """
    Runs process and ensures it is killed when the context is exited.
    """
    proc = subprocess.Popen(*args, **kwargs)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def get_experiment_status(experiment_dir):
    """
    Check the status of a Ray Tune experiment by reading its directory.

    Returns a dict with:
        - state: "not_started", "running", "finished"
        - num_trials: number of trials found
        - num_completed: number of trials that reported results
    """
    status = {
        "state": "not_started",
        "num_trials": 0,
        "num_completed": 0,
    }

    if not os.path.isdir(experiment_dir):
        return status

    # Check for experiment state file (created by Ray Tune)
    state_files = [
        f
        for f in os.listdir(experiment_dir)
        if f.startswith("experiment_state") and f.endswith(".json")
    ]
    if not state_files:
        return status

    try:
        analysis = ExperimentAnalysis(experiment_dir)
        results = analysis.results
        status["num_trials"] = len(results)
        status["num_completed"] = sum(1 for r in results.values() if r is not None)

        if status["num_completed"] == 0:
            status["state"] = "running"
        elif status["num_completed"] < status["num_trials"]:
            status["state"] = "running"
        else:
            status["state"] = "finished"
    except Exception:
        # Experiment directory exists but state is not yet readable.
        status["state"] = "running"

    return status


def stop_ray_cluster(timeout=RAY_SHUTDOWN_TIMEOUT):
    """
    Stop the Ray cluster, retrying until no nodes remain or timeout is reached.
    """
    start = time.time()
    while time.time() - start < timeout:
        status_proc = subprocess.run(
            "ray status", shell=True, capture_output=True, text=True
        )
        no_nodes = status_proc.returncode != 0

        stop_proc = subprocess.run(
            "ray stop", shell=True, capture_output=True, text=True
        )
        stop_ok = stop_proc.returncode in accepted_rc

        if no_nodes and stop_ok:
            return True
        time.sleep(5)

    raise RuntimeError(f"Failed to stop Ray cluster within {timeout} seconds")


class ResumeCheck(unittest.TestCase):
    # Only test 1 platform/design.
    platform = "asap7"
    design = "gcd"
    samples = 5
    iterations = 2
    experiment_name = "test-resume"

    def setUp(self):
        self.config = os.path.join(
            orfs_flow_dir,
            "designs",
            self.platform,
            self.design,
            "autotuner.json",
        )
        self.experiment_dir = os.path.join(
            orfs_flow_dir,
            "logs",
            self.platform,
            self.design,
            self.experiment_name,
        )
        self.jobs = self.samples
        self.num_cpus = os.cpu_count()

        # Fractional resources_per_trial avoids parallelism issues with Ray.
        res_per_trial = float("{:.1f}".format(self.num_cpus / self.samples))
        options = ["", "--resume"]
        self.executable = AutoTunerTestUtils.get_exec_cmd()
        self.commands = [
            f"{self.executable}"
            f" --design {self.design}"
            f" --platform {self.platform}"
            f" --config {self.config}"
            f" --jobs {self.jobs}"
            f" --experiment {self.experiment_name}"
            f" tune --iterations {self.iterations}"
            f" --samples {self.samples}"
            f" --resources_per_trial {res_per_trial}"
            f" {c}"
            for c in options
        ]

    def test_tune_resume(self):
        # Step 1: Run the first config (without --resume) asynchronously.
        # Wait until at least one trial has completed, then kill it.
        print("Step 1: Starting initial tuning run")
        with managed_process(self.commands[0].split()) as proc:
            start = time.time()
            while time.time() - start < POLL_TIMEOUT:
                status = get_experiment_status(self.experiment_dir)
                print(
                    f"  Status: {status['state']}, "
                    f"trials: {status['num_trials']}, "
                    f"completed: {status['num_completed']}"
                )
                if status["num_completed"] > 0:
                    print(
                        f"  {status['num_completed']} trial(s) completed, "
                        f"stopping initial run"
                    )
                    break
                time.sleep(POLL_INTERVAL)
            else:
                self.fail(f"No trials completed within {POLL_TIMEOUT} seconds")

        # Step 2: Stop the Ray cluster cleanly.
        print("Step 2: Stopping Ray cluster")
        stop_ray_cluster()
        print("  Ray cluster stopped")

        # Step 3: Run the second config (with --resume) to completion.
        print("Step 3: Resuming tuning run")
        proc = subprocess.run(self.commands[1].split())
        successful = proc.returncode in accepted_rc
        self.assertTrue(
            successful,
            f"Resume run failed with return code {proc.returncode}",
        )


if __name__ == "__main__":
    unittest.main()
