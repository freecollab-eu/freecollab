"""Await boolean browser conditions without treating their Promise as success."""
import math
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def wait_for_awaited_condition(page, expression, *, arg=None, timeout=30000, polling=100):
    if timeout <= 0 or polling <= 0:
        raise ValueError('Browser condition polling requires positive bounds.')
    deadline = time.monotonic() + timeout / 1000
    evaluate = '''async ({arg,budget})=>{
      let timer;
      try{return (await Promise.race([
        (PREDICATE)(arg),
        new Promise(resolve=>{timer=setTimeout(()=>resolve(false),budget)})
      ]))===true}finally{clearTimeout(timer)}
    }'''.replace('PREDICATE', expression)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PlaywrightTimeoutError(f'Asynchronous browser condition timed out after {timeout}ms.')
        result = page.evaluate(evaluate, {'arg': arg, 'budget': max(1, math.ceil(remaining * 1000))})
        if result is True and time.monotonic() <= deadline:
            return True
        remaining = deadline - time.monotonic()
        if remaining > 0:
            page.wait_for_timeout(min(polling, remaining * 1000))
