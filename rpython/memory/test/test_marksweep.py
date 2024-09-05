from rpython.memory.test.gc_test_base import GCTest

class TestSimpleMarkSweep(GCTest):
    from rpython.memory.gc.marksweep import SimpleMarkSweepGC as GCClass
