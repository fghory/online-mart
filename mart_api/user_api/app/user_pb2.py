# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: user.proto
"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\nuser.proto\x12\x04user\"\xa0\x01\n\x04User\x12\n\n\x02id\x18\x01 \x01(\x05\x12\x11\n\tuser_name\x18\x02 \x01(\t\x12\x12\n\nuser_email\x18\x03 \x01(\t\x12\x13\n\x0buser_cellno\x18\x04 \x01(\t\x12\x14\n\x0cuser_address\x18\x05 \x01(\t\x12\x18\n\x04role\x18\x06 \x01(\x0e\x32\n.user.Role\x12 \n\x06\x61\x63tion\x18\x07 \x01(\x0e\x32\x10.user.ActionType*\x1f\n\x04Role\x12\t\n\x05\x41\x44MIN\x10\x00\x12\x0c\n\x08\x43USTOMER\x10\x01*G\n\nActionType\x12\n\n\x06\x43REATE\x10\x00\x12\n\n\x06UPDATE\x10\x01\x12\n\n\x06\x44\x45LETE\x10\x02\x12\t\n\x05LOGIN\x10\x03\x12\n\n\x06LOGOUT\x10\x04\x62\x06proto3')

_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'user_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:

  DESCRIPTOR._options = None
  _ROLE._serialized_start=183
  _ROLE._serialized_end=214
  _ACTIONTYPE._serialized_start=216
  _ACTIONTYPE._serialized_end=287
  _USER._serialized_start=21
  _USER._serialized_end=181
# @@protoc_insertion_point(module_scope)
