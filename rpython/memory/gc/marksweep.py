from rpython.memory.gc.base import GCBase
from rpython.rlib.rarithmetic import ovfcheck
from rpython.rtyper.lltypesystem import lltype
from rpython.rtyper.lltypesystem import llmemory
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.lltypesystem.llmemory import raw_malloc

class SimpleMarkSweepGC(GCBase):
    HDR = lltype.Struct('header', ('tid', lltype.Signed))

    def setup(self):
        # convenient function for setup
        GCBase.setup(self)

    def malloc_fixedsize(self, typeid, size,
                               needs_finalizer=False,
                               is_finalizer_light=False,
                               contains_weakptr=False):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size

        result = raw_malloc(totalsize)
        return llmemory.cast_adr_to_ptr(result+size_gc_header, llmemory.GCREF)

    def malloc_varsize(self, typeid, length, size, itemsize,
                             offset_to_length):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        nonvarsize = size_gc_header + size
        try:
            varsize = ovfcheck(itemsize * length)
            totalsize = ovfcheck(nonvarsize + varsize)
        except OverflowError:
            raise memoryError

        result = raw_malloc(totalsize)
        (result + size_gc_header + offset_to_length).signed[0] = length
        res = llmemory.cast_adr_to_ptr(result+size_gc_header, llmemory.GCREF)
        return res

    def init_gc_object_immortal(self, addr, typeid16, flages=0):
        pass

    def collect(self):
        pass
