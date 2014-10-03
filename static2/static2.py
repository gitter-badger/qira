#!/usr/bin/env python2.7

# NO MORE RADARE
# tags should be dynamically generated
#   like when you request the 'instruction' tag, it triggers the disassembly
#   when you set the 'name' tag, it dedups names, and updates the reverse index
#   when you set the 'scope' tag, it adds it as a member of the function
# so it's a "managed" key value store
# don't worry at all about caching unless things are too slow

# stuff from Program should be moved here
#   this class should contain all of the information about an independent run of the binary
# move the webserver code out of here, and perhaps into qira_webserver


# *** EXISTING TAGS ***
# len -- bytes that go with this one
# name -- name of this address
# comment -- comment on this address
# instruction -- string of this instruction
# arch -- arch of this instruction
# crefs -- code xrefs


# objects are allowed in the key-value store,
#   but they should do something sane for the javascript on repr

# fhex and ghex shouldn't be used
# all addresses are numbers

import collections
import os, sys

import disasm
import loader
import Queue

# debugging
try:
  from hexdump import hexdump
except:
  pass

class Function:
  def __init__(self, start):
    self.start = start
    self.blocks = set()

  def __repr__(self):
    return hex(self.start) + " " + str(self.blocks)

  def add_block(self, block):
    self.blocks.add(block)

class Block:
  def __init__(self, start):
    self.__start__ = start
    self.addresses = set([start])

  def __repr__(self):
    return hex(self.start())+"-"+hex(self.end())

  def start(self):
    return self.__start__

  def end(self):
    return max(self.addresses)

  def add(self, address):
    self.addresses.add(address)

# allow for special casing certain tags
class Tags:
  def __init__(self, static, address):
    self.backing = {}
    self.static = static
    self.address = address

  def __getitem__(self, tag):
    # should reading the instruction tag trigger disasm?
    """
    if tag == "instruction":
      dat = self.static.memory(self.address, 0x10)
      return disasm.disasm(dat, self.address, self['arch'])
    """

    if tag in self.backing:
      return self.backing[tag]
    else:
      if tag == "crefs" or tag == "xrefs":
        # crefs has a default value of a new array
        self.backing[tag] = set()
        return self.backing[tag]
      if tag in static.global_tags:
        return static.global_tags[tag]
      return None

  def __setitem__(self, tag, val):
    if tag == "name":
      # name can change by adding underscores
      val = self.static.set_name(self.address, val)
    self.backing[tag] = val

# the new interface for all things static
# will only support radare2 for now
# mostly tags, except for names and functions
class Static:
  def __init__(self, path):
    self.tags = {}
    self.path = path

    # radare doesn't seem to have a concept of names
    # doesn't matter if this is in the python
    self.rnames = {}

    # fall through on an instruction
    # 'arch'
    self.global_tags = {}
    self.global_tags['functions'] = set()

    # concept from qira_program
    self.base_memory = {}

    # run the elf loader
    loader.load_binary(self, path)

  # this should be replaced with a 
  def set_name(self, address, name):
    if name not in self.rnames:
      self.rnames[name] = address
    else:
      # add underscore if name already exists
      return self.set_name(address, name+"_")
    return name

  def get_address_by_name(self, name):
    if name in self.rnames:
      return self.rnames[name]
    else:
      return None

  # keep the old tags interface
  # names and function data no longer stored here
  # things like xrefs can go here
  # only write functional tags here
  # comment     -- comment on this address
  # len         -- number of bytes grouped with this one
  # instruction -- string of this instruction
  # type        -- unset, 'instruction', 'data', 'string'
  def get_tags(self, filt, addresses=None):
    ret = {}
    if addresses == None:
      # all the addresses
      addresses = self.tags.keys()
    for a in addresses:
      rret = {}
      for f in filt:
        t = self.tags[a][f]
        if t != None:
          rret[f] = t
      if rret != {}:
        ret[a] = rret
    return ret
  
  def __setitem__(self, address, dat):
    if type(address) is str:
      self.global_tags[address] = dat

  # for a single address
  def __getitem__(self, address):
    if type(address) is str:
      if address in self.global_tags:
        return self.global_tags[address]
      else:
        return None
    if address not in self.tags:
      self.tags[address] = Tags(self, address)
    return self.tags[address]

  # return the memory at address:ln
  # replaces get_static_bytes
  def memory(self, address, ln):
    dat = []
    for i in range(ln):
      ri = address+i
      for (ss, se) in self.base_memory:
        if ss <= ri and ri < se:
          try:
            dat.append(self.base_memory[(ss,se)][ri-ss])
          except:
            return ''.join(dat)
    return ''.join(dat)

  def add_memory_chunk(self, address, dat):
    self.base_memory[(address, address+len(dat))] = dat

  # things to actually drive the static analyzer
  # runs the recursive descent parser at address
  # how to deal with block groupings?
  def make_function_at(self, address, recurse = True):
    block_starts = set([address])
    function_starts = set()
    this_function = Function(address)
    self['functions'].add(this_function)

    def disassemble(address):
      raw = self.memory(address, 0x10)
      d = disasm.disasm(raw, address, self[address]['arch'])
      self[address]['instruction'] = d
      self[address]['len'] = d.size()
      self[address]['function'] = this_function
      for (c,flag) in d.dests():
        if flag == disasm.DESTTYPE.call:
          function_starts.add(c)
          self[c]['xrefs'].add(address)
          # add this to the potential function boundary starts
          continue
        if c != address + d.size():
          self[c]['crefs'].add(address)
          block_starts.add(c)
      return d.dests()

    # recursive descent pass
    pending = Queue.Queue()
    done = set()
    pending.put(address)
    while not pending.empty():
      dests = disassemble(pending.get())
      for (d,flag) in dests:
        if flag == disasm.DESTTYPE.call:
          continue
        if d not in done:
          pending.put(d)
          done.add(d)
  
    #print map(hex, done)

    # block finding pass
    blocks = []
    for b in block_starts:
      this_block = Block(b)
      this_function.add_block(this_block)
      address = b
      i = self[address]['instruction']
      self[address]['block'] = this_block
      while not i.is_ending() and i.size() != 0:
        if address + i.size() in block_starts:
          break
        address += i.size()
        i = self[address]['instruction']
        this_block.add(address)
        self[address]['block'] = this_block
      blocks.append((b, address))

    # print blocks
    """
    for b in blocks:
      print hex(b[0]), hex(b[1]), self[b[0]]['crefs'], self[b[1]]['instruction'].dests()
      for a in range(b[0], b[1]+1):
        if self[a]['instruction'] != None:
          print "  ",hex(a),self[a]['instruction']
    """

    # find more functions
    for f in function_starts:
      if self[f]['function'] == None:
        self.make_function_at(f)


# *** STATIC TEST STUFF ***

if __name__ == "__main__":
  static = Static(sys.argv[1])
  print "arch:",static['arch']

  # find main
  main = static.get_address_by_name("main")
  print "main is at", hex(main)
  static.make_function_at(main)

  print "found %d functions" % len(static['functions'])

  # function printer
  for f in static['functions']:
    print f
    for b in sorted(f.blocks):
      print "  ",b
      for a in sorted(b.addresses):
        print "    ",hex(a),static[a]['instruction']



  #print static['functions']

  #print static[main]['instruction'], map(hex, static[main]['crefs'])
  #print static.get_tags(['name'])

