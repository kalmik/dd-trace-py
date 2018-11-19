"""
cleantest enables unittest test cases and suites to be run in separate python
interpreter instances, in parallel.
"""
from collections import namedtuple
import multiprocessing.dummy
import pickle
import unittest
import subprocess


DEFAULT_NUM_PROCS = 8


def cleantest(obj):
    """
    Marks a test case that is to be run in its own 'clean' interpreter instance.

    When applied to a TestCase class, each method will be run in a separate
    interpreter instance, in parallel.

    Usage on a class::

        @clean
        class PatchTests(object):
            # will be run in new interpreter
            def test_patch_before_import(self):
                patch()
                import module

            # will be run in new interpreter as well
            def test_patch_after_import(self):
                import module
                patch()


    Usage on a test method::

        class OtherTests(object):
            @clean
            def test_case(self):
                pass


    :param obj: method or class to run in a separate python interpreter.
    :return:
    """
    setattr(obj, '_test_clean', True)
    return obj


def is_iterable(i):
    try:
        iter(i)
    except TypeError:
        return False
    else:
        return True


def is_cleantest(test):
    if hasattr(test, '_test_clean'):
        return True
    if hasattr(test, '_testMethodName'):
        t = getattr(test, test._testMethodName)
        if hasattr(t, '_test_clean'):
            return True
    return False


class CleanTestSuite(unittest.TestSuite):
    TestResult = namedtuple('TestResult', 'test returncode output')

    def __init__(self, modprefix, *args, **kwargs):
        self.modprefix = modprefix
        self.num_procs = kwargs.get('num_procs', DEFAULT_NUM_PROCS)
        super(CleanTestSuite, self).__init__(*args, **kwargs)

    @staticmethod
    def merge_result(into_result, new_result):
        into_result.failures += new_result.failures
        into_result.errors += new_result.errors
        into_result.skipped += new_result.skipped
        into_result.expectedFailures += new_result.expectedFailures
        into_result.unexpectedSuccesses += new_result.unexpectedSuccesses
        into_result.testsRun += new_result.testsRun

    @staticmethod
    def get_tests_from_suite(suite):
        tests = []
        clean_tests = []
        suites_to_check = [suite]
        while suites_to_check:
            suite = suites_to_check.pop()
            for t in suite:
                if is_iterable(t):
                    if is_cleantest(t):
                        suites_to_check.append([cleantest(s) for s in t])
                    else:
                        suites_to_check.append(t)
                elif is_cleantest(t):
                    clean_tests.append(t)
                else:
                    tests.append(t)
        return tests, clean_tests

    @staticmethod
    def test_name(test):
        return '{}.{}'.format(unittest.util.strclass(test.__class__), test._testMethodName)

    def full_test_mod_name(self, test):
        name = self.test_name(test)
        testcase_name = '{}.{}'.format(self.modprefix, name)
        return testcase_name

    def run_test_in_subprocess(self, test):
        # DEV: We need to handle when unittest adds its own test case, which we
        # can't run in a new process. Typically these test cases have to do
        # with exceptions raised at import time.
        if test.__class__.__module__.startswith('unittest'):
            result = unittest.TestResult()
            test(result)
            return result

        testcase_name = self.full_test_mod_name(test)
        try:
            output = subprocess.check_output(
                ['python', '-m', 'tests.cleantestrunner', testcase_name],
                stderr=subprocess.STDOUT,  # cleantestrunner outputs to stderr
            )
            result = pickle.loads(output)
        except subprocess.CalledProcessError as err:
            result = pickle.loads(err.output)
        return result

    def run(self, result, debug=False):
        tests, clean_tests = self.get_tests_from_suite(self._tests)
        pool = multiprocessing.dummy.Pool(self.num_procs)

        # run each regular test
        for test in tests:
            test(result)

        # run the clean tests in a pool
        test_results = pool.map(self.run_test_in_subprocess, clean_tests)
        for new_result in test_results:
            self.merge_result(result, new_result)
        return result


def _close_prefix_clean_test_suite(modprefix):
    def get_clean_test_suite(*args, **kwargs):
        return CleanTestSuite(modprefix, *args, **kwargs)
    return get_clean_test_suite


class CleanTestLoader(unittest.TestLoader):
    def __init__(self, modprefix, *args, **kwargs):
        self.suiteClass = _close_prefix_clean_test_suite(modprefix)
        super(CleanTestLoader, self).__init__(*args, **kwargs)