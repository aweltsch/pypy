from rpython.rlib.debug import ll_assert
from rpython.memory.gc.base import GCBase
from rpython.rlib.rarithmetic import ovfcheck
from rpython.rtyper.lltypesystem import lltype, llmemory, llgroup
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.lltypesystem.llmemory import NULL, raw_malloc, raw_free
from rpython.rtyper.lltypesystem.llmemory import raw_malloc_usage
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
        self.objects_with_weakrefs = self.AddressStack()
        self.objects_with_finalizers = self.AddressDeque()
        self.collection_lock = False
        self.heap_growth_factor = 2
        self.initial_heap_size = 200
        self.prev_heap_size = self.initial_heap_size
        self.cur_heap_size = 0

    def malloc_fixedsize(self, typeid, size,
                               needs_finalizer=False,
                               is_finalizer_light=False,
                               contains_weakptr=False):
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size

        if raw_malloc_usage(totalsize) + self.cur_heap_size > self.heap_growth_factor * self.prev_heap_size:
            self.collect()
        result = raw_malloc(totalsize)
        self.init_gc_object(result, typeid)

        self.address_space.append(result)

        if needs_finalizer:
            from rpython.rtyper.lltypesystem import rffi
            self.objects_with_finalizers.append(result + size_gc_header)
            self.objects_with_finalizers.append(rffi.cast(llmemory.Address, -1))

        if contains_weakptr:
            self.objects_with_weakrefs.append(result + size_gc_header)

        self.cur_heap_size += raw_malloc_usage(totalsize)
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

        if raw_malloc_usage(totalsize) + self.cur_heap_size > self.heap_growth_factor * self.prev_heap_size:
            self.collect()
        result = raw_malloc(totalsize)
        self.init_gc_object(result, typeid)

        self.address_space.append(result)

        (result + size_gc_header + offset_to_length).signed[0] = length
        res = llmemory.cast_adr_to_ptr(result+size_gc_header, llmemory.GCREF)
        self.cur_heap_size += raw_malloc_usage(totalsize)
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
                hdr.tid = hdr.tid & ~GCFLAG_SURVIVING # clear flags
                new_heap.append(addr)
            else:
                size = self.get_size(addr + self.gcheaderbuilder.size_gc_header)
                self.cur_heap_size -= raw_malloc_usage(size)
                ll_assert(self.cur_heap_size >= 0, "miscalculation")
                raw_free(addr)

        self.address_space.delete()
        self.address_space = new_heap

    def register_finalizer(self, fq_index, gcobj):
        from rpython.rtyper.lltypesystem import rffi
        obj = llmemory.cast_ptr_to_adr(gcobj)
        fq_index = rffi.cast(llmemory.Address, fq_index)
        self.objects_with_finalizers.append(obj)
        self.objects_with_finalizers.append(fq_index)

    def collect(self, generation=0):
        if self.collection_lock:
            return
        self.collection_lock = True
        self.mark()
        if self.objects_with_finalizers.non_empty():
            scan = self.deal_with_objects_with_finalizers()
        # there's some work we need to do before we can do a "simple" sweep
        if self.objects_with_weakrefs.non_empty():
            self.invalidate_weakrefs()
        # we first need to sweep objects that are definitely unreachable
        # only after that we can execute the finalizers
        # (any object with finalizer should be marked surviving)
        # this is important because finalizers can introduce new objects
        # which are not marked as surviving
        self.sweep()
        self.execute_finalizers()
        self.prev_heap_size = self.cur_heap_size
        self.collection_lock = False


    def deal_with_objects_with_finalizers(self):
        new_with_finalizer = self.AddressDeque()
        finalising = self.AddressDeque()

        while self.objects_with_finalizers.non_empty():
            x = self.objects_with_finalizers.popleft()
            fq_nr = self.objects_with_finalizers.popleft()
            if self.surviving(x):
                new_with_finalizer.append(x)
                new_with_finalizer.append(fq_nr)
            else:
                from rpython.rtyper.lltypesystem import rffi
                fq_index = rffi.cast(lltype.Signed, fq_nr)
                self.mark_finalizer_to_run(fq_index, x)
                finalising.append(x)

        # revive objects for finalizer runs
        while finalising.non_empty():
            x = finalising.popleft()
            SimpleMarkSweepGC.mark_recursive(x, self)

        self.objects_with_finalizers.delete()
        self.objects_with_finalizers = new_with_finalizer
        finalising.delete()

    def invalidate_weakrefs(self):
        # walk over list of objects that contain weakrefs
        # if the object it references survives then update the weakref
        # otherwise invalidate the weakref
        new_with_weakref = self.AddressStack()
        while self.objects_with_weakrefs.non_empty():
            obj = self.objects_with_weakrefs.pop()
            if not self.surviving(obj):
                continue # weakref itself dies
            offset = self.weakpointer_offset(self.get_type_id(obj))
            pointing_to = (obj + offset).address[0]
            # XXX I think that pointing_to cannot be NULL here
            if pointing_to:
                if self.surviving(pointing_to):
                    new_with_weakref.append(obj)
                else:
                    (obj + offset).address[0] = NULL
        self.objects_with_weakrefs.delete()
        self.objects_with_weakrefs = new_with_weakref
