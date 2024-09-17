from rpython.memory.gc.base import GCBase
from rpython.rlib.rarithmetic import ovfcheck
from rpython.rtyper.lltypesystem import lltype, llmemory, llgroup
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.lltypesystem.llmemory import raw_malloc, raw_free
from rpython.rlib.rarithmetic import LONG_BIT

first_gcflag = 1 << (LONG_BIT//2)
GCFLAG_SURVIVING = first_gcflag
GCFLAG_EXTERNAL = first_gcflag << 1
GCFLAG_FINALIZER_REACHABLE = first_gcflag << 2

class SimpleMarkSweepGC(GCBase):
    HDR = lltype.Struct('header', ('tid', lltype.Signed))

    def setup(self):
        # convenient function for setup
        GCBase.setup(self)
        self.address_space = self.AddressDeque()

    def malloc_fixedsize(self, typeid, size,
                               needs_finalizer=False,
                               is_finalizer_light=False,
                               contains_weakptr=False):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size

        result = raw_malloc(totalsize)
        self.init_gc_object(result, typeid)

        self.address_space.append(result)
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
        self.init_gc_object(result, typeid)

        self.address_space.append(result)

        (result + size_gc_header + offset_to_length).signed[0] = length
        res = llmemory.cast_adr_to_ptr(result+size_gc_header, llmemory.GCREF)
        return res

    def init_gc_object_immortal(self, addr, typeid16, flags=0):
        self.init_gc_object(addr, typeid16, flags | GCFLAG_EXTERNAL | GCFLAG_SURVIVING)

    def init_gc_object(self, addr, typeid16, flags=0):
        hdr = llmemory.cast_adr_to_ptr(addr, lltype.Ptr(self.HDR))
        hdr.tid = self.combine(typeid16, flags)

    def get_type_id(self, addr):
        # NOTE: this is necessary in BaseGC.trace
        tid = self.header(addr).tid
        return llop.extract_ushort(llgroup.HALFWORD, tid)

    # NOTE: looks like most GC classes use the same implementation
    def combine(self, typeid16, flags):
        return llop.combine_ushort(lltype.Signed, typeid16, flags)

    def mark_surviving_callback(self, obj, stack):
        addr = obj.address[0] # ?!?
        hdr = self.header(addr)
        if hdr.tid & GCFLAG_SURVIVING == 0:
            # hasn't been visited yet, recurse
            stack.append(addr)
            hdr.tid |= GCFLAG_SURVIVING

    @staticmethod
    def mark_recursive(root_addr, self):
        # TODO BFS (queue) might make more sense
        stack = self.AddressStack()
        hdr = self.header(root_addr)
        hdr.tid |= GCFLAG_SURVIVING
        stack.append(root_addr)
        while stack.non_empty():
            addr = stack.pop()
            self.trace(addr, self.make_callback('mark_surviving_callback'), self, stack)
        stack.delete()

    def mark(self):
        self.enumerate_all_roots(SimpleMarkSweepGC.mark_recursive, self)

    def _teardown(self):
        # TODO: is it OK here to iterate through all allocated objects to free them?
        pass

    def surviving(self, obj):
        hdr = self.header(obj)
        return hdr.tid & GCFLAG_SURVIVING != 0

    def sweep(self):
        new_heap = self.AddressDeque()
        prev_heap = self.address_space
        while prev_heap.non_empty():
            addr = prev_heap.popleft()
            hdr = llmemory.cast_adr_to_ptr(addr, lltype.Ptr(self.HDR))
            if hdr.tid & GCFLAG_SURVIVING:
                new_heap.append(addr)
            else:
                raw_free(addr)
                
        self.address_space.delete()
        self.address_space = new_heap

    def collect(self, generation=0):
        self.mark()
        self.sweep()
