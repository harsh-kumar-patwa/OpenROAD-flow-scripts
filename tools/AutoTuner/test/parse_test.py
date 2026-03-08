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

import os
import tempfile
import textwrap
import unittest
import yaml
from unittest.mock import patch, MagicMock

from autotuner.utils import parse_flow_variables, parse_tunable_variables


class TestParseTunableVariables(unittest.TestCase):
    """Tests for parse_tunable_variables() which reads variables.yaml
    and returns tunable variable names."""

    def test_returns_set(self):
        """parse_tunable_variables should return a set."""
        result = parse_tunable_variables()
        self.assertIsInstance(result, set)

    def test_returns_nonempty(self):
        """There should be at least one tunable variable defined."""
        result = parse_tunable_variables()
        self.assertGreater(len(result), 0)

    def test_known_tunable_variables_present(self):
        """Known tunable variables from variables.yaml should be present."""
        result = parse_tunable_variables()
        expected_tunable = {
            "CORE_UTILIZATION",
            "CORE_AREA",
            "PLACE_DENSITY_LB_ADDON",
            "CTS_BUF_DISTANCE",
        }
        for var in expected_tunable:
            self.assertIn(var, result, f"{var} should be tunable")

    def test_non_tunable_variables_excluded(self):
        """Variables without tunable: 1 should not be in the result."""
        result = parse_tunable_variables()
        non_tunable = {
            "EQUIVALENCE_CHECK",
            "DETAILED_METRICS",
            "SKIP_REPORT_METRICS",
        }
        for var in non_tunable:
            self.assertNotIn(var, result, f"{var} should not be tunable")

    def _run_with_yaml(self, yaml_data):
        """Helper to run parse_tunable_variables with custom YAML data."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
            yaml.dump(yaml_data, tmp)
            tmp_path = tmp.name
        try:
            with patch("autotuner.utils.os.path.realpath", return_value=tmp_path):
                with patch(
                    "autotuner.utils.os.path.dirname",
                    return_value=os.path.dirname(tmp_path),
                ):
                    with patch(
                        "autotuner.utils.os.path.join",
                        return_value=tmp_path,
                    ):
                        return parse_tunable_variables()
        finally:
            os.unlink(tmp_path)

    def test_filters_tunable_flag(self):
        """Only variables with tunable == 1 should be returned."""
        mock_yaml = {
            "VAR_A": {"description": "desc", "tunable": 1},
            "VAR_B": {"description": "desc", "tunable": 0},
            "VAR_C": {"description": "desc"},
            "VAR_D": {"description": "desc", "tunable": 1},
        }
        result = self._run_with_yaml(mock_yaml)
        self.assertEqual(result, {"VAR_A", "VAR_D"})

    def test_empty_yaml(self):
        """An empty YAML file should return an empty set."""
        result = self._run_with_yaml({})
        self.assertEqual(result, set())

    def test_no_tunable_variables(self):
        """When no variables have tunable: 1, return empty set."""
        mock_yaml = {
            "VAR_A": {"description": "desc", "tunable": 0},
            "VAR_B": {"description": "desc"},
        }
        result = self._run_with_yaml(mock_yaml)
        self.assertEqual(result, set())


class TestParseFlowVariables(unittest.TestCase):
    """Tests for parse_flow_variables() which runs make vars and
    parses env() patterns from Tcl scripts."""

    def _setup_flow_dir(self, tmpdir, tcl_content, vars_content=""):
        """Helper to create flow directory structure with Tcl files."""
        flow_dir = os.path.join(tmpdir, "flow")
        scripts_dir = os.path.join(flow_dir, "scripts")
        os.makedirs(scripts_dir)

        tcl_file = os.path.join(scripts_dir, "test.tcl")
        with open(tcl_file, "w") as f:
            f.write(tcl_content)

        vars_file = os.path.join(flow_dir, "vars.tcl")
        with open(vars_file, "w") as f:
            f.write(vars_content)

        return tcl_file

    @patch("autotuner.utils.subprocess.run")
    def test_makefile_failure_exits(self, mock_run):
        """Should exit when make vars fails."""
        mock_run.return_value = MagicMock(returncode=1)
        with self.assertRaises(SystemExit):
            parse_flow_variables("/fake/base", "asap7")

    @patch("autotuner.utils.subprocess.run")
    def test_calls_make_with_platform(self, mock_run):
        """Should call make with correct platform argument."""
        mock_run.return_value = MagicMock(returncode=1)
        try:
            parse_flow_variables("/some/base", "sky130hd")
        except SystemExit:
            pass
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertIn("PLATFORM=sky130hd", call_args)
        self.assertIn("vars", call_args)

    @patch("autotuner.utils.glob.glob")
    @patch("autotuner.utils.subprocess.run")
    def test_parses_env_patterns(self, mock_run, mock_glob):
        """Should extract variable names from env() patterns in Tcl files."""
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tcl_content = textwrap.dedent("""\
                source $::env(SCRIPTS_DIR)/load.tcl
                set density $::env(PLACE_DENSITY)
                puts $::env(RESULTS_DIR)/output.odb
            """)
            vars_content = textwrap.dedent("""\
                set ::env(CORE_UTILIZATION) 40
                set ::env(CTS_BUF_DISTANCE) 100
            """)
            tcl_file = self._setup_flow_dir(tmpdir, tcl_content, vars_content)
            mock_glob.return_value = [tcl_file]

            with patch("autotuner.utils.os.path.exists", return_value=True):
                result = parse_flow_variables(tmpdir, "asap7")

            self.assertIn("SCRIPTS_DIR", result)
            self.assertIn("PLACE_DENSITY", result)
            self.assertIn("RESULTS_DIR", result)
            self.assertIn("CORE_UTILIZATION", result)
            self.assertIn("CTS_BUF_DISTANCE", result)

    @patch("autotuner.utils.glob.glob")
    @patch("autotuner.utils.subprocess.run")
    def test_variables_are_uppercased(self, mock_run, mock_glob):
        """Variables should be uppercased in the result."""
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tcl_file = self._setup_flow_dir(tmpdir, "$::env(some_variable)\n")
            mock_glob.return_value = [tcl_file]

            with patch("autotuner.utils.os.path.exists", return_value=True):
                result = parse_flow_variables(tmpdir, "asap7")

            self.assertIn("SOME_VARIABLE", result)
            self.assertNotIn("some_variable", result)

    @patch("autotuner.utils.glob.glob")
    @patch("autotuner.utils.subprocess.run")
    def test_deduplicates_variables(self, mock_run, mock_glob):
        """Duplicate env() references should produce a single entry."""
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tcl_file = self._setup_flow_dir(tmpdir, "$::env(MY_VAR)\n$::env(MY_VAR)\n")
            mock_glob.return_value = [tcl_file]

            with patch("autotuner.utils.os.path.exists", return_value=True):
                result = parse_flow_variables(tmpdir, "asap7")

            self.assertIsInstance(result, set)
            self.assertIn("MY_VAR", result)

    @patch("autotuner.utils.subprocess.run")
    def test_missing_vars_tcl_exits(self, mock_run):
        """Should exit when vars.tcl is not generated."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch("autotuner.utils.os.path.exists", return_value=False):
            with self.assertRaises(SystemExit):
                parse_flow_variables("/fake/base", "asap7")


if __name__ == "__main__":
    unittest.main()
