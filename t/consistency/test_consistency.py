import asyncio
import random
import subprocess
from t.consistency.consistency_checker import ConsistencyChecker


class Stresser(object):

    def __init__(self, num_workers, num_producers, loop):
        self.workers = set(range(num_workers))
        self._worker_procs = {}
        self.num_workers = num_workers
        self.num_producers = num_producers
        self.producers = set(range(num_producers))
        self._producer_procs = {}
        self.loop = loop
        self._stop_stresser = asyncio.Event(loop=loop)

    @property
    def _stopped(self):
        return self.workers - self._running

    @property
    def _running(self):
        return set(self._worker_procs)

    @property
    def _stopped_producers(self):
        return self.producers - self._running_producers

    @property
    def _running_producers(self):
        return set(self._producer_procs)

    async def _run_stresser(self):
        print('Running stresser')
        while not self._stop_stresser.is_set():
            print('Stresser iteration')
            if self._should():
                await self._maybe_stop_worker()
            if self._should():
                await self._maybe_spawn_worker()
            await asyncio.sleep(random.uniform(1, 20))

    def stop_stresser(self):
        print('Stopping stresser')
        self._stop_stresser.set()

    def _should(self):
        return (
            random.choices([True, False], [0.75, 0.25], k=1)[0] and
            not self._stop_stresser.is_set()
        )

    async def _maybe_stop_worker(self):
        print('Maybe stop')
        if len(self._running) > 1:
            await self._stop_worker(random.choice(list(self._running)))

    async def _maybe_spawn_worker(self):
        print('Maybe start')
        if self._stopped:
            await self._start_worker(random.choice(list(self._stopped)))

    async def start(self, stopped_at_start=0):
        assert stopped_at_start < self.num_workers
        start_workers = random.sample(list(self.workers),
                                      self.num_workers - stopped_at_start)
        print(f'Start workers: {start_workers!r}')
        await asyncio.wait([self._start_producer(producer)
                            for producer in self.producers])
        await asyncio.wait([self._start_worker(worker)
                            for worker in start_workers],
                           loop=self.loop, return_when=asyncio.ALL_COMPLETED)
        asyncio.ensure_future(self._run_stresser(), loop=loop)

    async def _start_worker(self, worker):
        assert worker in self.workers
        with open(f'worker_{worker}.logs', 'w') as f:
            if worker not in self._worker_procs:
                print(f'Starting worker {worker}')
                self._worker_procs[worker] = await asyncio.create_subprocess_exec(
                    'faust',
                    '-A', 'examples.simple',
                    'worker',
                    '-l', 'info',
                    '--web-port', str(8080 + worker),
                    stdout=f,
                    stderr=subprocess.STDOUT,
                )

    async def stop_all(self):
        await asyncio.wait(
            [self._stop_worker(worker) for worker in self._running],
            loop=self.loop, return_when=asyncio.ALL_COMPLETED,
        )

    async def stop_all_producers(self):
        await asyncio.wait(
            [self._stop_producer(producer)
             for producer in self._running_producers],
            loop=self.loop, return_when=asyncio.ALL_COMPLETED,
        )

    async def _stop_worker(self, worker):
        assert worker in self.workers
        print(f'Stopping worker {worker}')
        proc = self._worker_procs.pop(worker)
        await self._stop_process(proc)

    async def _start_producer(self, producer):
        assert producer in self.producers
        if producer not in self._producer_procs:
            with open(f'producer_{producer}.logs', 'w') as f:
                print(f'Starting producer: {producer}')
                self._producer_procs[producer] = await asyncio.create_subprocess_exec(
                    'python',
                    '/Users/vineet/faust/examples/simple.py',
                    'produce',
                    '-l', 'info',
                    stdout=f,
                    stderr=subprocess.STDOUT,
                )

    async def _stop_producer(self, producer):
        assert producer in self.producers
        print(f'Stopping producer {producer}')
        proc = self._producer_procs.pop(producer)
        await self._stop_process(proc)

    async def _stop_process(self, proc):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        await proc.wait()


async def test_consistency(loop):
    stresser = Stresser(num_workers=3, num_producers=4, loop=loop)
    print('Starting stresser')
    await stresser.start(stopped_at_start=1)
    print('Waiting for stresser to run')
    await asyncio.sleep(180)  # seconds to run stresser for
    print('Stopping all producers')
    await stresser.stop_all_producers()
    stresser.stop_stresser()
    print('Waiting for consumer lag to be 0')
    await asyncio.sleep(30)  # wait for consumer lag to reach 0
    print('Stopping everything')
    await stresser.stop_all()
    checker = ConsistencyChecker('withdrawals',
                                 'f-simple-user_to_total-changelog', loop=loop)
    await checker.check_consistency()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_consistency(loop))