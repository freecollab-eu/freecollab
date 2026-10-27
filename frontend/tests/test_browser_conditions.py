"""Finite real-browser controls for awaited asynchronous proof conditions."""
import os
import shutil
import time
import unittest

from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright
from browser_conditions import wait_for_awaited_condition


class AwaitedBrowserConditions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),
            headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def test_false_promises_are_retried_until_actual_true(self):
        self.page.evaluate('window.attempts=0')
        self.assertIs(wait_for_awaited_condition(self.page, '''async()=>{
          await new Promise(resolve=>setTimeout(resolve,5));return ++window.attempts===3;
        }''', timeout=2000, polling=10), True)
        self.assertEqual(self.page.evaluate('window.attempts'), 3)

    def test_resolved_false_times_out_instead_of_passing(self):
        with self.assertRaises(PlaywrightTimeoutError):
            wait_for_awaited_condition(self.page, 'async()=>false', timeout=150, polling=10)

    def test_unresolved_promise_is_bounded_by_original_deadline(self):
        started = time.monotonic()
        with self.assertRaises(PlaywrightTimeoutError):
            wait_for_awaited_condition(self.page, '()=>new Promise(()=>{})', timeout=150)
        self.assertLess(time.monotonic() - started, 3)

    def test_truthy_non_boolean_is_not_success(self):
        with self.assertRaises(PlaywrightTimeoutError):
            wait_for_awaited_condition(self.page, 'async()=>({ready:true})', timeout=150)

    def test_actual_evaluation_error_propagates(self):
        with self.assertRaisesRegex(Error, 'deliberate predicate failure') as caught:
            wait_for_awaited_condition(self.page, 'async()=>{throw Error("deliberate predicate failure")}', timeout=1000)
        self.assertNotIsInstance(caught.exception, PlaywrightTimeoutError)

    def test_structured_arguments_are_not_interpolated_into_javascript(self):
        value = {'text': 'quotes " \' ` ${not_code}', 'expected': 7}
        self.assertIs(wait_for_awaited_condition(self.page,
            'async arg=>arg.text.includes("not_code")&&arg.expected===7', arg=value, timeout=1000), True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
