# SPDX-License-Identifier: AGPL-3.0-only
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from gateway import Refused
from local_agent_vector_resources import Capacity,Demand,VectorResources

def d(worker,ram,vram):
    return Demand(worker,Capacity(ram,vram))

class VectorResourceTests(unittest.TestCase):
    def test_exact_dual_capacity_and_release(self):
        l=VectorResources(Capacity(64,16))
        a,b=l.reserve_wave("run",(d("a",32,8),d("b",32,8)))
        self.assertEqual(l.snapshot()[2],Capacity(0,0))
        l.cancel(a)
        self.assertEqual(l.snapshot()[1],Capacity(64,16))
        l.release_reaped(a)
        self.assertEqual(l.snapshot()[2],Capacity(32,8))
        l.release_reaped(b)
        self.assertEqual(l.snapshot()[1],Capacity(0,0))

    def test_vram_shortage_is_atomic(self):
        l=VectorResources(Capacity(64,15))
        with self.assertRaises(Refused):
            l.reserve_wave("run",(d("a",32,8),d("b",32,8)))
        self.assertEqual(l.snapshot()[1],Capacity(0,0))
        self.assertEqual(len(l.reserve_wave("run",(d("a",32,8),))),1)

    def test_ram_shortage_is_atomic(self):
        l=VectorResources(Capacity(63,16))
        with self.assertRaises(Refused):
            l.reserve_wave("run",(d("a",32,8),d("b",32,8)))
        self.assertEqual(l.snapshot()[1],Capacity(0,0))

    def test_cpu_only_is_not_implicit_gpu_fallback(self):
        l=VectorResources(Capacity(64,0))
        with self.assertRaises(Refused):
            l.reserve_wave("run",(d("a",32,1),))
        l.reserve_wave("run",(d("a",32,0),))
        self.assertEqual(l.snapshot()[1],Capacity(32,0))

    def test_resize_below_active_retains_ownership(self):
        l=VectorResources(Capacity(64,16))
        a,=l.reserve_wave("run",(d("a",32,8),))
        l.resize(Capacity(16,4))
        self.assertEqual(l.snapshot(),(Capacity(16,4),Capacity(32,8),Capacity(0,0)))
        with self.assertRaises(Refused):
            l.reserve_wave("next",(d("b",1,0),))
        l.release_reaped(a)
        self.assertEqual(l.snapshot()[2],Capacity(16,4))

    def test_wrong_and_replayed_handles_refused(self):
        l=VectorResources(Capacity(64,16))
        a,=l.reserve_wave("run",(d("a",32,8),))
        for fake in (replace(a),replace(a,run="other")):
            with self.assertRaises(Refused):
                l.release_reaped(fake)
        l.release_reaped(a)
        with self.assertRaises(Refused):
            l.release_reaped(a)
        with self.assertRaises(Refused):
            l.reserve_wave("run",(d("a",32,8),))
        l.reserve_wave("retry",(d("a",32,8),))

    def test_invalid_dimensions_and_duplicates(self):
        for value in (Capacity(True,1),Capacity(-1,0),Capacity(1,1048577),Capacity(1,1.0)):
            with self.assertRaises(Refused):
                VectorResources(value)
        l=VectorResources(Capacity(64,16))
        for demands in ((d("a",0,0),),(d("a",1,1),d("a",1,1)),(d("a",True,1),),()):
            with self.assertRaises(Refused):
                l.reserve_wave("run",demands)
        self.assertEqual(l.snapshot()[1],Capacity(0,0))

    def test_competing_waves_cannot_oversubscribe(self):
        l=VectorResources(Capacity(64,16))
        barrier=Barrier(2,timeout=5)
        def reserve(name):
            barrier.wait()
            try:
                return l.reserve_wave(name,(d("a",32,8),d("b",32,8)))
            except Refused:
                return ()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=tuple(pool.map(reserve,("first","second")))
        self.assertEqual(sorted(map(len,results)),[0,2])
        self.assertEqual(l.snapshot()[1],Capacity(64,16))

    def test_close_keeps_active_until_terminal(self):
        l=VectorResources(Capacity(64,16))
        a,=l.reserve_wave("run",(d("a",32,8),))
        l.close()
        with self.assertRaises(Refused):
            l.resize(Capacity(128,32))
        with self.assertRaises(Refused):
            l.reserve_wave("retry",(d("a",1,1),))
        l.cancel(a)
        self.assertEqual(l.snapshot()[1],Capacity(32,8))
        l.release_reaped(a)
        self.assertEqual(l.snapshot()[1],Capacity(0,0))
