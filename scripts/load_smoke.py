"""Bounded, read-only smoke load for the shared Oncomap VPS."""

import argparse
import concurrent.futures
import statistics
import time
import urllib.error
import urllib.request


PATHS = ('/', '/applications/', '/quality/login/', '/quality/health/')


def fetch(base, index):
    url = base.rstrip('/') + PATHS[index % len(PATHS)]
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            response.read()
            return response.status, time.monotonic() - started, url
    except (urllib.error.URLError, TimeoutError) as error:
        return str(error), time.monotonic() - started, url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='https://oncomap-abai.kz')
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--concurrency', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.requests <= 1000 or not 1 <= args.concurrency <= 50:
        parser.error('Limit: 1-1000 requests and 1-50 concurrent workers')
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda index: fetch(args.base, index), range(args.requests)))
    durations = sorted(result[1] for result in results)
    bad = [result for result in results if result[0] != 200]
    print(f'Requests: {len(results)}; concurrency: {args.concurrency}; '
          f'elapsed: {time.monotonic() - started:.2f}s')
    print(f'p50: {statistics.median(durations):.3f}s; '
          f'p95: {durations[int(0.95 * (len(durations) - 1))]:.3f}s; '
          f'max: {durations[-1]:.3f}s; errors: {len(bad)}')
    for result in bad[:10]:
        print('ERROR', result)
    if bad:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
