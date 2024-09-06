from rpython.memory.gc.base import GCBase
from rpython.rtyper.lltypesystem import lltype

class SimpleMarkSweepGC(GCBase):
    HDR = lltype.Struct('header', ('tid', lltype.Signed))

    def malloc_fixedsize(self, typeid, size,
                               needs_finalizer=False,
                               is_finalizer_light=False,
                               contains_weakptr=False):
        pass

    def malloc_varsize(self, typeid, length, size, itemsize,
                             offset_to_length):
        pass

    def init_gc_object_immortal(self, addr, typeid16, flages=0):
        pass

    def collect(self):
        pass
