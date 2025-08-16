from __future__ import annotations

import re
import struct
import math
import unicodedata
import time
import bpy
import bmesh
import mathutils
import numpy as np
from mathutils import Vector, Quaternion, Matrix
from pathlib import Path
from . import common
from . import compat
from . translations.pgettext_functions import *
from . fileutil import serialize_to_file
from . import misc_DOPESHEET_MT_editor_menus

from CM3D2.Serialization.Files import Anm  # type: ignore
from CM3D2.Serialization.Performance import PerformanceExtensions  # type: ignore
from System import Array  # type: ignore


# メインオペレーター
@compat.BlRegister()
class CNV_OT_export_cm3d2_anm(bpy.types.Operator):
    bl_idname = 'export_anim.export_cm3d2_anm'
    bl_label = "CM3D2モーション (.anm)"
    bl_description = "カスタムメイド3D2のanmファイルを保存します"
    bl_options = {'REGISTER'}

    filepath = bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = '.anm'
    filter_glob = bpy.props.StringProperty(default='*.anm', options={'HIDDEN'})

    scale = bpy.props.FloatProperty(name="Scale", default=0.2, min=0.1, max=100, soft_min=0.1, soft_max=100, step=100, precision=1, description="Scale factor for mesh export")
    is_backup = bpy.props.BoolProperty(name="Backup File", default=True, description="Create backup file when overwriting")
    version = bpy.props.IntProperty(name="File Version", default=1000, min=1000, max=1111, soft_min=1000, soft_max=1111, step=1)
    
    #is_anm_data_text = bpy.props.BoolProperty(name="From Anm Text", default=False, description="Input data from JSON file")
    items = [
        ('ALL'       , "Bake All Frames"      , "Export every frame as a keyframe (legacy behavior, large file sizes)", 'SEQUENCE' , 1),
        ('KEYED'     , "Only Export Keyframes", "Only export keyframes and their tangents (for more advance users)"   , 'KEYINGSET', 2),
        ('DIRECT'    , "Direct Serialization", "Uses AnmBuilder + CM3D2Serializer pipeline for optimal compatibility", 'EXPORT', 4),
        ('TEXT'      , "From Anm Text JSON"   , "Export data from the JSON in the 'AnmData' text file"                , 'TEXT'     , 3)
    ]
    export_method = bpy.props.EnumProperty(items=items, name="Export Method", default='ALL')


    frame_start = bpy.props.IntProperty(name="Start Frame", default=0, min=0, max=99999, soft_min=0, soft_max=99999, step=1)
    frame_end = bpy.props.IntProperty(name="End Frame", default=0, min=0, max=99999, soft_min=0, soft_max=99999, step=1)
    key_frame_count = bpy.props.IntProperty(name="Keyframe Count", default=-1, min=-1, max=99999, soft_min=1, soft_max=99999, step=1)
    time_scale = bpy.props.FloatProperty(name="Playback Speed", default=1.0, min=0.1, max=10.0, soft_min=0.1, soft_max=10.0, step=10, precision=1)
    is_keyframe_clean = bpy.props.BoolProperty(name="Clean Duplicate Keyframes", default=True)
    is_visual_transform = bpy.props.BoolProperty(name="Use Visual Transforms", default=True )
    is_smooth_handle = bpy.props.BoolProperty(name="Smooth Interpolation", default=True)

    items = [
        ('ARMATURE', "Armature", "Use armature bone hierarchy", 'OUTLINER_OB_ARMATURE', 1),
        ('ARMATURE_PROPERTY', "Armature Properties", "Use bone data from armature properties", 'ARMATURE_DATA', 2),
    ]
    bone_parent_from = bpy.props.EnumProperty(items=items, name="Bone Parent Source", default='ARMATURE_PROPERTY')
    
    is_location = bpy.props.BoolProperty(name="Export Location"  , default=True )
    is_rotation = bpy.props.BoolProperty(name="Export Rotation"  , default=True )
    is_scale    = bpy.props.BoolProperty(name="Export Scale (Ex)", default=False)

    is_remove_unkeyed_bone       = bpy.props.BoolProperty(name="Remove Unkeyed Bones", default=False)
    is_remove_alone_bone         = bpy.props.BoolProperty(name="Remove Orphan Bones", default=True, description="Remove bones with no parent or children")
    is_remove_ik_bone            = bpy.props.BoolProperty(name="Remove IK/Nub Bones", default=True, description="Remove bones with IK or Nub in name")
    is_remove_serial_number_bone = bpy.props.BoolProperty(name="Remove Numbered Bones", default=True, description="Remove bones with serial numbers in name")
    is_remove_japanese_bone      = bpy.props.BoolProperty(name="Remove Japanese Named Bones", default=True, description="Remove bones with Japanese characters")
    
    # Direct serialization specific options
    direct_export_all_frames     = bpy.props.BoolProperty(name="Export All Frames", default=False, description="Export every frame (larger files) vs keyframes only (smaller files)")
    
    items = [
        ('SIMPLE', "Simple Sampling", "Sample every Nth frame uniformly", 'MOD_DECIM', 1),
        ('DENSITY', "Smart Density", "Adaptive sampling based on bone keyframe density", 'FILTER', 2),
        ('MOTION', "Motion Analysis", "Advanced motion-based keyframe detection (experimental)", 'TRACKING', 3),
        ('RDP', "RDP Algorithm", "Ramer-Douglas-Peucker curve simplification (mathematical optimal)", 'MESH_DATA', 4)
    ]
    direct_optimization_mode     = bpy.props.EnumProperty(items=items, name="Optimization Mode", default='DENSITY', description="Method used for keyframe reduction")
    
    # Simple mode options
    direct_simple_step           = bpy.props.IntProperty(name="Frame Step", default=2, min=2, max=10, step=1, description="Keep every Nth frame (2 = every 2nd frame)")
    
    # Density mode options  
    direct_density_threshold     = bpy.props.FloatProperty(name="Dense Bone Threshold", default=0.8, min=0.1, max=1.0, step=0.1, precision=1, description="Bones with keyframes above this ratio are considered dense and thinned out")
    direct_dense_reduction       = bpy.props.IntProperty(name="Dense Bone Sampling", default=2, min=2, max=5, step=1, description="Keep every Nth frame for dense bones (2 = every 2nd frame)")
    
    # Motion mode options
    direct_motion_threshold      = bpy.props.FloatProperty(name="Motion Threshold", default=0.001, min=0.0001, max=0.1, step=0.001, precision=4, description="Minimum motion change to keep keyframe")
    direct_time_gap_limit        = bpy.props.IntProperty(name="Max Time Gap", default=10, min=3, max=30, step=1, description="Maximum frames between keyframes")
    
    # RDP mode options
    direct_rdp_tolerance         = bpy.props.FloatProperty(name="RDP Tolerance", default=0.01, min=0.001, max=1.0, step=0.001, precision=3, description="Maximum deviation allowed - lower = higher quality, larger files")
    direct_rdp_min_distance      = bpy.props.IntProperty(name="Min Frame Distance", default=2, min=1, max=10, step=1, description="Minimum frames between keyframes")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob and ob.type == 'ARMATURE':
            return True
        return False

    def invoke(self, context, event):
        prefs = common.preferences()

        ob = context.active_object
        arm = ob.data
        action_name = None
        if ob.animation_data and ob.animation_data.action:
            action_name = common.remove_serial_number(ob.animation_data.action.name)

        if prefs.anm_default_path:
            self.filepath = common.default_cm3d2_dir(prefs.anm_default_path, action_name, self.filename_ext)
        else:
            self.filepath = common.default_cm3d2_dir(prefs.anm_export_path, action_name, self.filename_ext)
        self.frame_start = context.scene.frame_start
        self.frame_end = context.scene.frame_end
        self.scale = 1.0 / prefs.scale
        self.is_backup = bool(prefs.backup_ext)
        self.key_frame_count = -1

        if "BoneData:0" in arm:
            self.bone_parent_from = 'ARMATURE_PROPERTY'
        else:
            self.bone_parent_from = 'ARMATURE'

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.prop(self, 'scale')

        box = self.layout.box()
        box.prop(self, 'is_backup', icon='FILE_BACKUP')
        box.prop(self, 'version')

        #self.layout.prop(self, 'is_anm_data_text', icon='TEXT')
        box = self.layout.box()
        box.label(text="Export Method")
        box.prop(self, 'export_method', expand=True)

        box = self.layout.box()
        box.enabled = not (self.export_method == 'TEXT')
        box.prop(self, 'time_scale')
        
        # Show different UI based on export method
        if self.export_method == 'DIRECT':
            # Direct serialization method - optimized settings
            sub_box = box.box()
            sub_box.label(text="Direct Serialization Settings", icon='EXPORT')
            row = sub_box.row()
            row.prop(self, 'frame_start')
            row.prop(self, 'frame_end')
            sub_box.prop(self, 'is_smooth_handle', icon='SMOOTHCURVE')
            
            # File size vs quality option
            size_box = sub_box.box()
            size_box.prop(self, 'direct_export_all_frames', icon='SEQUENCE')
            if self.direct_export_all_frames:
                size_box.label(text="⚠ Large file sizes - every frame exported", icon='ERROR')
            else:
                size_box.label(text="✓ Optimal file sizes - keyframes only", icon='CHECKMARK')
            
            # Technical info for developers
            info_box = box.box()
            info_box.label(text="Uses AnmBuilder → CM3D2Serializer → MemoryStream pipeline", icon='INFO')
            if self.direct_export_all_frames:
                info_box.label(text="Exports all frames for maximum compatibility")
            else:
                info_box.label(text="Exports keyframes only for optimal file size")
        else:
            # Traditional methods - full UI
            sub_box = box.box()
            sub_box.enabled = (self.export_method == 'ALL')
            row = sub_box.row()
            row.prop(self, 'frame_start')
            row.prop(self, 'frame_end')
            sub_box.prop(self, 'key_frame_count')
            sub_box.prop(self, 'is_keyframe_clean', icon='DISCLOSURE_TRI_DOWN')
            sub_box.prop(self, 'is_smooth_handle', icon='SMOOTHCURVE')

        sub_box = box.box()
        sub_box.label(text="Bone Parent Source", icon='FILE_PARENT')
        sub_box.prop(self, 'bone_parent_from', icon='FILE_PARENT', expand=True)
        
        sub_box = box.box()
        sub_box.label(text="Export Animation Data")
        column = sub_box.column(align=True)
        column.prop(self, 'is_location', icon=compat.icon('CON_LOCLIKE' ))
        column.prop(self, 'is_rotation', icon=compat.icon('CON_ROTLIKE' ))
        column.prop(self, 'is_scale'   , icon=compat.icon('CON_SIZELIKE'))

        # Bone filtering - hide for Modern method as it uses minimal filtering
        if self.export_method != 'DIRECT':
            sub_box = box.box()
            sub_box.label(text="Bone Filtering", icon='X')
            column = sub_box.column(align=True)
            column.prop(self, 'is_remove_unkeyed_bone'      , icon='KEY_DEHLT'              )
            column.prop(self, 'is_remove_alone_bone'        , icon='UNLINKED'               )
            column.prop(self, 'is_remove_ik_bone'           , icon='CONSTRAINT_BONE'        )
            column.prop(self, 'is_remove_serial_number_bone', icon='SEQUENCE'               )
            column.prop(self, 'is_remove_japanese_bone'     , icon=compat.icon('HOLDOUT_ON'))
        else:
            # For Direct serialization, full filtering options available
            sub_box = box.box()
            sub_box.label(text="Bone Filtering (Direct Serialization)", icon='BONE_DATA')
            column = sub_box.column(align=True)
            column.prop(self, 'is_remove_unkeyed_bone'      , icon='KEY_DEHLT'              )
            column.prop(self, 'is_remove_alone_bone'        , icon='UNLINKED'               )
            column.prop(self, 'is_remove_ik_bone'           , icon='CONSTRAINT_BONE'        )
            column.prop(self, 'is_remove_serial_number_bone', icon='SEQUENCE'               )
            column.prop(self, 'is_remove_japanese_bone'     , icon=compat.icon('HOLDOUT_ON'))
            
            # Keyframe optimization options (only when not exporting all frames)
            if not self.direct_export_all_frames:
                opt_box = sub_box.box()
                opt_box.label(text="Keyframe Optimization", icon='SEQUENCE')
                opt_box.prop(self, 'direct_optimization_mode', expand=True)
                
                if self.direct_optimization_mode == 'SIMPLE':
                    simple_box = opt_box.box()
                    simple_box.prop(self, 'direct_simple_step', icon='MOD_DECIM')
                    
                elif self.direct_optimization_mode == 'DENSITY':
                    density_box = opt_box.box()
                    density_box.prop(self, 'direct_density_threshold', icon='FILTER')
                    density_box.prop(self, 'direct_dense_reduction', icon='MOD_DECIM')
                    
                elif self.direct_optimization_mode == 'MOTION':
                    motion_box = opt_box.box()
                    motion_box.label(text="⚠ Experimental - may be slower", icon='ERROR')
                    motion_box.prop(self, 'direct_motion_threshold', icon='TRACKING')
                    motion_box.prop(self, 'direct_time_gap_limit', icon='TIME')
                    
                elif self.direct_optimization_mode == 'RDP':
                    rdp_box = opt_box.box()
                    rdp_box.label(text="🎯 Mathematical optimal curve simplification", icon='INFO')
                    rdp_box.prop(self, 'direct_rdp_tolerance', icon='MESH_DATA')
                    rdp_box.prop(self, 'direct_rdp_min_distance', icon='DRIVER_DISTANCE')
                    rdp_box.label(text="Lower tolerance = higher quality", icon='HELP')
        
        file_select_params: bpy.types.FileSelectParams = None
        try:
            file_select_params = context.screen.areas[0].spaces[0].params
            if not isinstance(file_select_params, bpy.types.FileSelectParams):
                file_select_params = None
        except:
            pass
        
        if file_select_params is not None:
            path = Path(file_select_params.filename)
            if self.is_ex_anm:
                if path.suffix == '.anm' and not path.stem.endswith('.ex'):
                    path = path.with_stem(path.stem + '.ex')
            else:
                if path.suffix == '.anm' and path.stem.endswith('.ex'):
                    path = path.with_stem(path.stem.removesuffix('.ex'))
            file_select_params.filename = str(path)

    @property
    def is_ex_anm(self) -> bool:
        return self.is_scale
    
    def execute(self, context):
        
        # Don't allow exporting extended animation as '.anm' instead of '.ex.anm'
        if self.is_ex_anm and self.filepath.endswith('.anm') and not self.filepath.endswith('.ex.anm'):
            self.report(
                type={'ERROR'}, 
                message=f_tip_("Blocked attempt to export extended animation as '.anm', use '.ex.anm' instead")
            )
            return {'CANCELLED'}
        
        common.preferences().anm_export_path = self.filepath

        try:
            file = common.open_temporary(self.filepath, 'wb', is_backup=self.is_backup)
        except:
            self.report(
                type={'ERROR'}, 
                message=f_tip_("ファイルを開くのに失敗しました、アクセス不可かファイルが存在しません。file={}", self.filepath)
            )
            return {'CANCELLED'}

        try:
            with file:
                if self.export_method == 'TEXT':
                    self.write_animation_from_text(context, file)
                elif self.export_method == 'DIRECT':
                    self.write_animation_direct_method(context, file)
                else:
                    builder = self.get_anm_builder()
                    anm = builder.build_anm(context)
                    serialize_to_file(anm, file)
        except common.CM3D2ExportError as e:
            self.report(type={'ERROR'}, message=str(e))
            return {'CANCELLED'}

        return {'FINISHED'}

    def write_animation_OLD(self, context, file):
        """Legacy manual binary serialization method (deprecated)"""
        # Original implementation removed - use write_animation_direct_method() instead
        pass

    def write_animation_direct_method(self, context, file):
        """Direct serialization using AnmBuilder + CM3D2Serializer pipeline"""
        try:
            from CM3D2.Serialization import CM3D2Serializer
            from System.IO import MemoryStream
        except ImportError as e:
            raise common.CM3D2ExportError(f"Required serialization libraries not available: {e}")
        
        builder = AnmBuilder(reporter=self)
        builder.scale = self.scale
        builder.version = self.version
        builder.frame_start = self.frame_start
        builder.frame_end = self.frame_end
        builder.export_method = 'ALL' if self.direct_export_all_frames else 'DIRECT_OPTIMIZED'
        builder.is_visual_transform = True
        builder.is_remove_unkeyed_bone = self.is_remove_unkeyed_bone
        builder.bone_parent_from = self.bone_parent_from
        builder.is_location = self.is_location
        builder.is_rotation = self.is_rotation
        builder.is_scale = self.is_scale
        builder.time_scale = self.time_scale
        builder.optimization_mode = self.direct_optimization_mode
        builder.simple_step = self.direct_simple_step
        builder.density_threshold = self.direct_density_threshold
        builder.dense_reduction = self.direct_dense_reduction
        builder.motion_threshold = self.direct_motion_threshold
        builder.time_gap_limit = self.direct_time_gap_limit
        builder.rdp_tolerance = self.direct_rdp_tolerance
        builder.rdp_min_distance = self.direct_rdp_min_distance
        
        # Direct serialization settings
        builder.is_keyframe_clean = False
        builder.is_smooth_handle = self.is_smooth_handle
        builder.is_remove_alone_bone = self.is_remove_alone_bone
        builder.is_remove_ik_bone = self.is_remove_ik_bone
        builder.is_remove_serial_number_bone = self.is_remove_serial_number_bone
        builder.is_remove_japanese_bone = self.is_remove_japanese_bone
        
        anm = builder.build_anm(context)
        
        # Serialize and convert to Python bytes
        serializer = CM3D2Serializer()
        memory_stream = MemoryStream()
        serializer.Serialize(memory_stream, anm)
        
        # Optimized: Use ToArray() for better performance than Array.Copy()
        python_buffer = bytes(memory_stream.ToArray())
        
        file.write(python_buffer)
        
        self.report(type={'INFO'}, message=f"Animation exported via direct serialization ({len(python_buffer)} bytes)")

    def write_animation_from_text(self, context, file):
        txt = context.blend_data.texts.get("AnmData")
        if not txt:
            raise common.CM3D2ExportError("There is no 'AnmData' text file.")

        import json
        anm_data = json.loads(txt.as_string())

        common.write_str(file, 'CM3D2_ANIM')
        file.write(struct.pack('<i', self.version))

        for base_bone_name, bone_data in anm_data.items():
            path = bone_data['path']
            file.write(struct.pack('<?', True))
            common.write_str(file, path)

            for channel_id, channel in bone_data['channels'].items():
                file.write(struct.pack('<B', int(channel_id)))
                channel_data_count = len(channel)
                file.write(struct.pack('<i', channel_data_count))
                for channel_data in channel:
                    frame = channel_data['frame']
                    data = ( channel_data['f0'], channel_data['f1'], channel_data['f2'] )
                    file.write(struct.pack('<f' , frame))
                    file.write(struct.pack('<3f', *data ))

        file.write(struct.pack('<?', False))

    def get_anm_builder(self) -> AnmBuilder:
        builder = AnmBuilder(reporter=self)
        builder.scale                        = self.scale
        builder.version                      = self.version
        builder.export_method                = self.export_method
        builder.frame_start                  = self.frame_start
        builder.frame_end                    = self.frame_end
        builder.key_frame_count              = self.key_frame_count
        builder.time_scale                   = self.time_scale
        builder.is_keyframe_clean            = self.is_keyframe_clean
        builder.is_visual_transform          = self.is_visual_transform
        builder.is_smooth_handle             = self.is_smooth_handle
        builder.bone_parent_from             = self.bone_parent_from
        builder.is_location                  = self.is_location
        builder.is_rotation                  = self.is_rotation
        builder.is_scale                     = self.is_scale
        builder.is_remove_unkeyed_bone       = self.is_remove_unkeyed_bone
        builder.is_remove_alone_bone         = self.is_remove_alone_bone
        builder.is_remove_ik_bone            = self.is_remove_ik_bone
        builder.is_remove_serial_number_bone = self.is_remove_serial_number_bone
        builder.is_remove_japanese_bone      = self.is_remove_japanese_bone
        return builder
        
    

class AnmBuilder:
    def __init__(self, reporter: bpy.types.Operator):
        self.reporter = reporter
        
        self.scale = 0.2
        self.version = 1000
        self.export_method = 'ALL'
        self.frame_start = 0
        self.frame_end = 0
        self.key_frame_count = -1
        self.time_scale = 1
        self.is_keyframe_clean = True
        self.is_visual_transform = True
        self.is_smooth_handle = True
        self.bone_parent_from = 'ARMATURE_PROPERTY'
        self.is_location = True
        self.is_rotation = True
        self.is_scale    = False
        self.is_remove_unkeyed_bone       = False
        self.is_remove_alone_bone         = True
        self.is_remove_ik_bone            = True
        self.is_remove_serial_number_bone = True
        self.is_remove_japanese_bone      = True
        
        
        self.no_set_frame = False
        
        self._invalid_bones: dict[bpy.types.PoseBone, list[tuple(float, Matrix)]] = dict()
    
    def build_anm(self, context) -> Anm:
        obj = context.active_object
        arm = obj.data
        
        bone_parents = self.get_bone_parents(arm, self.bone_parent_from == 'ARMATURE_PROPERTY')
        
        bones, anm_data_raw = self.collect_raw_animation_data(context, obj, bone_parents)

        fps = context.scene.render.fps
        time_step = 1 / fps * (1.0 / self.time_scale)
        
        track_data = self.get_track_data(anm_data_raw)
        
        anm = self.assemble_anm(
            bone_parents, bones, track_data, time_step,
            auto_smooth=(self.is_smooth_handle and (self.export_method == 'ALL' or self.export_method == 'DIRECT_OPTIMIZED'))
        )

        return anm

    def get_animation_frames(self, context, pose, bones, bone_parents):
        fps = context.scene.render.fps
        
        anm_data_raw: dict[str, Track] = {}
        
        class Track(dict):
            def __init__(self):
                super().__init__()
                self['LOC'] = {}
                self['ROT'] = {}
                self['SCL'] = {}
            @property
            def loc_dict(self) -> dict[float, Vector]:
                return self['LOC']
            @property
            def rot_dict(self) -> dict[float, Quaternion]:
                return self['ROT']
            @property
            def scl_dict(self) -> dict[float, Vector]:
                return self['SCL']
        
        key_frame_count = self.key_frame_count
        if key_frame_count == -1:
            key_frame_count = (self.frame_end - self.frame_start) + 1
            
        same_locs: dict[str, Vector    ] = {}
        same_rots: dict[str, Quaternion] = {}
        same_scls: dict[str, Vector    ] = {}
        pre_rots = {}
        for key_frame_index in range(key_frame_count):
            if key_frame_count == 1:
                frame = self.frame_start
            else:
                frame = (self.frame_end - self.frame_start) / (key_frame_count - 1) * key_frame_index + self.frame_start
            if not self.no_set_frame:
                context.scene.frame_set(frame=int(frame), subframe=frame - int(frame))
                if compat.IS_LEGACY:
                    context.scene.update()
                else:
                    layer = context.view_layer
                    layer.update()

            time = (frame - self.frame_start) / fps * (1.0 / self.time_scale)
            
            for bone in bones:
                if bone.name not in anm_data_raw:
                    anm_data_raw[bone.name] = Track()
                    same_locs[bone.name] = []
                    same_rots[bone.name] = []
                    same_scls[bone.name] = []

                pose_bone = pose.bones[bone.name]
                pose_mat: Matrix = pose_bone.matrix.copy() #ob.convert_space(pose_bone=pose_bone, matrix=pose_bone.matrix, from_space='POSE', to_space='WORLD')
                parent = bone_parents[bone.name]
                if parent:
                    pose_mat = compat.convert_bl_to_cm_bone_rotation(pose_mat)
                    parent_space = self.try_get_bone_inverse(pose.bones[parent.name], frame)
                    if parent_space is None:
                        continue
                    pose_mat = compat.mul(parent_space, pose_mat)
                    pose_mat = compat.convert_bl_to_cm_bone_space(pose_mat)
                else:
                    pose_mat = compat.convert_bl_to_cm_bone_rotation(pose_mat)
                    pose_mat = compat.convert_bl_to_cm_space(pose_mat)
                
                loc = pose_mat.to_translation() * self.scale
                rot = pose_mat.to_quaternion()
                scl = pose_mat.to_scale()

                # This fixes rotations that jump to alternate representations.
                if bone.name in pre_rots:
                    if 5.0 < pre_rots[bone.name].rotation_difference(rot).angle:
                        rot.w, rot.x, rot.y, rot.z = -rot.w, -rot.x, -rot.y, -rot.z
                pre_rots[bone.name] = rot.copy()
                
                if (not self.is_keyframe_clean 
                    or key_frame_index == 0 
                    or key_frame_index == key_frame_count - 1
                    or len(anm_data_raw[bone.name].loc_dict) == 0):
                    
                    anm_data_raw[bone.name].loc_dict[time] = loc.copy()
                    anm_data_raw[bone.name].rot_dict[time] = rot.copy()
                    anm_data_raw[bone.name].scl_dict[time] = scl.copy()

                    if self.is_keyframe_clean:
                        same_locs[bone.name].append(KeyFrame(time, loc.copy()))
                        same_rots[bone.name].append(KeyFrame(time, rot.copy()))
                        same_scls[bone.name].append(KeyFrame(time, scl.copy()))
                else:
                    
                    new_same_list, new_keydict = self.determine_new_keyframe(time, same_locs[bone.name].copy(), loc)
                    same_locs[bone.name] = new_same_list
                    anm_data_raw[bone.name].loc_dict.update(new_keydict)
                    
                    #a = same_rots[bone.name][-1].value - rot
                    #b = same_rots[bone.name][-1].slope
                    #if self.check_is_mismatch(a.w, b.w) or self.check_is_mismatch(a.x, b.x) or self.check_is_mismatch(a.y, b.y) or self.check_is_mismatch(a.z, b.z):
                    #    if 2 <= len(same_rots[bone.name]):
                    #        anm_data_raw[bone.name].rot_dict[same_rots[bone.name][-1].time] = same_rots[bone.name][-1].value.copy()
                    #    anm_data_raw[bone.name].rot_dict[time] = rot.copy()
                    #    same_rots[bone.name] = [KeyFrame(time, rot.copy(), a.copy())] # update last position and slope
                    #else:
                    #    same_rots[bone.name].append(KeyFrame(time, rot.copy(), b.copy())) # update last position, but not last slope

                    new_same_list, new_keydict = self.determine_new_keyframe(time, same_rots[bone.name].copy(), rot)
                    same_rots[bone.name] = new_same_list
                    anm_data_raw[bone.name].rot_dict.update(new_keydict)
                    
                    new_same_list, new_keydict = self.determine_new_keyframe(time, same_scls[bone.name].copy(), scl)
                    same_scls[bone.name] = new_same_list
                    anm_data_raw[bone.name].scl_dict.update(new_keydict)
        

        self.report_invalid_bones()

        return anm_data_raw
    
    def get_direct_keyframes_optimized(self, context, pose, bones, bone_parents, fcurves):
        """Optimized keyframe collection using ALL method's stable logic
        but only sampling at actual keyframe times for smaller file sizes"""
        fps = context.scene.render.fps
        
        anm_data_raw: dict[str, Track] = {}
        
        class Track(dict):
            def __init__(self):
                super().__init__()
                self['LOC'] = {}
                self['ROT'] = {}
                self['SCL'] = {}
            @property
            def loc_dict(self) -> dict[float, Vector]:
                return self['LOC']
            @property
            def rot_dict(self) -> dict[float, Quaternion]:
                return self['ROT']
            @property
            def scl_dict(self) -> dict[float, Vector]:
                return self['SCL']
        
        # Multi-mode keyframe optimization
        if self.optimization_mode == 'SIMPLE':
            keyframe_times = self._get_simple_keyframes()
        elif self.optimization_mode == 'DENSITY':
            keyframe_times = self._get_density_keyframes_cached(bones, fcurves)
        elif self.optimization_mode == 'MOTION':
            keyframe_times = self._get_motion_keyframes_cached(bones, fcurves)
        elif self.optimization_mode == 'RDP':
            keyframe_times = self._get_rdp_keyframes_cached(bones, fcurves)
        else:
            keyframe_times = self._get_density_keyframes_cached(bones, fcurves)  # Default fallback
        
        # Debug info
        reduction_ratio = (1 - len(keyframe_times) / (self.frame_end - self.frame_start + 1)) * 100
        self.reporter.report(type={'INFO'}, message=f"Direct Optimized: {len(keyframe_times)} keyframes (vs {self.frame_end - self.frame_start + 1} total) - {reduction_ratio:.1f}% reduction")
        
        # Use ALL method's proven pose matrix logic for each keyframe time
        pre_rots = {}
        for frame in keyframe_times:
            if not self.no_set_frame:
                context.scene.frame_set(frame=int(frame), subframe=frame - int(frame))
                if compat.IS_LEGACY:
                    context.scene.update()
                else:
                    layer = context.view_layer
                    layer.update()

            time = (frame - self.frame_start) / fps * (1.0 / self.time_scale)
            
            for bone in bones:
                if bone.name not in anm_data_raw:
                    anm_data_raw[bone.name] = Track()

                pose_bone = pose.bones[bone.name]
                pose_mat: Matrix = pose_bone.matrix.copy()
                parent = bone_parents[bone.name]
                if parent:
                    pose_mat = compat.convert_bl_to_cm_bone_rotation(pose_mat)
                    parent_space = self.try_get_bone_inverse(pose.bones[parent.name], frame)
                    if parent_space is None:
                        continue
                    pose_mat = compat.mul(parent_space, pose_mat)
                    pose_mat = compat.convert_bl_to_cm_bone_space(pose_mat)
                else:
                    pose_mat = compat.convert_bl_to_cm_bone_rotation(pose_mat)
                    pose_mat = compat.convert_bl_to_cm_space(pose_mat)
                
                loc = pose_mat.to_translation() * self.scale
                rot = pose_mat.to_quaternion()
                scl = pose_mat.to_scale()

                # Apply rotation jump fix from ALL method (same threshold as original)
                if bone.name in pre_rots:
                    if 5.0 < pre_rots[bone.name].rotation_difference(rot).angle:
                        rot.w, rot.x, rot.y, rot.z = -rot.w, -rot.x, -rot.y, -rot.z
                pre_rots[bone.name] = rot.copy()
                
                # Store keyframe data WITHOUT tangents (like ALL method)
                anm_data_raw[bone.name].loc_dict[time] = loc.copy()
                anm_data_raw[bone.name].rot_dict[time] = rot.copy()
                anm_data_raw[bone.name].scl_dict[time] = scl.copy()

        self.report_invalid_bones()
        return anm_data_raw
    
    def _get_simple_keyframes(self):
        """Simple uniform sampling - every Nth frame"""
        keyframes = []
        for frame in range(self.frame_start, self.frame_end + 1, self.simple_step):
            keyframes.append(frame)
        
        # Always include start and end
        keyframes.extend([self.frame_start, self.frame_end])
        return sorted(set(keyframes))
    
    def _cache_fcurves_for_bones(self, bones, fcurves):
        """Cache FCurve lookups to avoid repeated finds - performance optimization"""
        fcurve_cache = {}
        prop_sizes = {'location': 3, 'rotation_quaternion': 4, 'rotation_euler': 3, 'scale': 3}
        
        for bone in bones:
            rna_data_stub = f'pose.bones["{bone.name}"]'
            fcurve_cache[bone.name] = {}
            
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                fcurve_cache[bone.name][prop] = []
                for axis_index in range(prop_sizes[prop]):
                    fcurve = fcurves.find(rna_data_stub + '.' + prop, index=axis_index)
                    fcurve_cache[bone.name][prop].append(fcurve)
        
        return fcurve_cache
    
    def _get_density_keyframes_cached(self, bones, fcurves):
        """Smart density-based sampling with cached FCurve lookups"""
        fcurve_cache = self._cache_fcurves_for_bones(bones, fcurves)
        
        all_keyframes = set()
        all_keyframes.add(self.frame_start)  # Always include start
        all_keyframes.add(self.frame_end)    # Always include end
        
        for bone in bones:
            bone_keyframes = set()
            
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                for fcurve in fcurve_cache[bone.name][prop]:
                    if fcurve:
                        for keyframe in fcurve.keyframe_points:
                            bone_keyframes.add(int(keyframe.co[0]))
            
            # Check density: if bone has many keyframes (dense), thin it out
            if bone_keyframes:
                total_frames = self.frame_end - self.frame_start + 1
                density_ratio = len(bone_keyframes) / total_frames
                
                if density_ratio > self.density_threshold:  # Dense keyframes
                    # Keep every Nth keyframe for dense animation
                    sorted_keyframes = sorted(bone_keyframes)
                    thinned = sorted_keyframes[::self.dense_reduction]  # Every Nth frame
                    all_keyframes.update(thinned)
                else:  # Sparse keyframes
                    # Keep all keyframes for sparse animation (position bones, etc.)
                    all_keyframes.update(bone_keyframes)
        
        return sorted(all_keyframes)
    
    def _get_density_keyframes(self, bones, fcurves):
        """Smart density-based sampling"""
        all_keyframes = set()
        all_keyframes.add(self.frame_start)  # Always include start
        all_keyframes.add(self.frame_end)    # Always include end
        
        for bone in bones:
            rna_data_stub = f'pose.bones["{bone.name}"]'
            bone_keyframes = set()
            
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                prop_sizes = {'location': 3, 'rotation_quaternion': 4, 'rotation_euler': 3, 'scale': 3}
                for axis_index in range(prop_sizes[prop]):
                    fcurve = fcurves.find(rna_data_stub + '.' + prop, index=axis_index)
                    if fcurve:
                        for keyframe in fcurve.keyframe_points:
                            bone_keyframes.add(int(keyframe.co[0]))
            
            # Check density: if bone has many keyframes (dense), thin it out
            if bone_keyframes:
                total_frames = self.frame_end - self.frame_start + 1
                density_ratio = len(bone_keyframes) / total_frames
                
                if density_ratio > self.density_threshold:  # Dense keyframes
                    # Keep every Nth keyframe for dense animation
                    sorted_keyframes = sorted(bone_keyframes)
                    thinned = sorted_keyframes[::self.dense_reduction]  # Every Nth frame
                    all_keyframes.update(thinned)
                else:  # Sparse keyframes
                    # Keep all keyframes for sparse animation (position bones, etc.)
                    all_keyframes.update(bone_keyframes)
        
        return sorted(all_keyframes)
    
    def _get_motion_keyframes_cached(self, bones, fcurves):
        """Advanced motion-based keyframe detection with cached FCurve lookups"""
        fcurve_cache = self._cache_fcurves_for_bones(bones, fcurves)
        
        significant_keyframes = set()
        significant_keyframes.add(self.frame_start)  # Always include start
        significant_keyframes.add(self.frame_end)    # Always include end
        
        for bone in bones:
            bone_keyframes = []
            
            # Collect all keyframes for this bone using cached FCurves
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                for axis_index, fcurve in enumerate(fcurve_cache[bone.name][prop]):
                    if fcurve:
                        for keyframe in fcurve.keyframe_points:
                            bone_keyframes.append((keyframe.co[0], keyframe.co[1], prop, axis_index))
            
            # Sort by time and detect significant changes
            bone_keyframes.sort(key=lambda x: x[0])
            if bone_keyframes:
                # Always include first keyframe
                significant_keyframes.add(bone_keyframes[0][0])
                
                # Add keyframes with significant motion changes
                for i in range(1, len(bone_keyframes)):
                    prev_frame, prev_value, prev_prop, prev_axis = bone_keyframes[i-1]
                    curr_frame, curr_value, curr_prop, curr_axis = bone_keyframes[i]
                    
                    # Only compare same property and axis
                    if prev_prop == curr_prop and prev_axis == curr_axis:
                        value_change = abs(curr_value - prev_value)
                        time_gap = curr_frame - prev_frame
                        
                        # Consider significant if large value change or long time gap
                        is_significant = (value_change > self.motion_threshold or time_gap > self.time_gap_limit)
                        
                        if is_significant:
                            significant_keyframes.add(curr_frame)
        
        return sorted(significant_keyframes)
    
    def _get_rdp_keyframes_cached(self, bones, fcurves):
        """Ramer-Douglas-Peucker curve simplification with cached FCurve lookups"""
        fcurve_cache = self._cache_fcurves_for_bones(bones, fcurves)
        
        rdp_keyframes = set()
        rdp_keyframes.add(self.frame_start)  # Always include start
        rdp_keyframes.add(self.frame_end)    # Always include end
        
        for bone in bones:
            # Process each animation channel separately using cached FCurves
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                for axis_index, fcurve in enumerate(fcurve_cache[bone.name][prop]):
                    if fcurve and len(fcurve.keyframe_points) > 2:
                        # Extract keyframe data as (frame, value) points
                        points = [(kf.co[0], kf.co[1]) for kf in fcurve.keyframe_points]
                        points.sort(key=lambda x: x[0])  # Sort by frame
                        
                        # Apply RDP algorithm to this curve
                        if len(points) > 2:
                            simplified_points = self._rdp_simplify(points, self.rdp_tolerance)
                            
                            # Add frames to keyframe set, respecting min distance
                            for frame, value in simplified_points:
                                rdp_keyframes.add(int(frame))
        
        # Enforce minimum frame distance
        if self.rdp_min_distance > 1:
            rdp_keyframes = self._enforce_min_distance(sorted(rdp_keyframes), self.rdp_min_distance)
        
        return sorted(rdp_keyframes)
    
    def _get_motion_keyframes(self, bones, fcurves):
        """Advanced motion-based keyframe detection"""
        significant_keyframes = set()
        significant_keyframes.add(self.frame_start)  # Always include start
        significant_keyframes.add(self.frame_end)    # Always include end
        
        for bone in bones:
            rna_data_stub = f'pose.bones["{bone.name}"]'
            bone_keyframes = []
            
            # Collect all keyframes for this bone
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                prop_sizes = {'location': 3, 'rotation_quaternion': 4, 'rotation_euler': 3, 'scale': 3}
                for axis_index in range(prop_sizes[prop]):
                    fcurve = fcurves.find(rna_data_stub + '.' + prop, index=axis_index)
                    if fcurve:
                        for keyframe in fcurve.keyframe_points:
                            bone_keyframes.append((keyframe.co[0], keyframe.co[1], prop, axis_index))
            
            # Sort by time and detect significant changes
            bone_keyframes.sort(key=lambda x: x[0])
            if bone_keyframes:
                # Always include first keyframe
                significant_keyframes.add(bone_keyframes[0][0])
                
                # Add keyframes with significant motion changes
                for i in range(1, len(bone_keyframes)):
                    prev_frame, prev_value, prev_prop, prev_axis = bone_keyframes[i-1]
                    curr_frame, curr_value, curr_prop, curr_axis = bone_keyframes[i]
                    
                    # Only compare same property and axis
                    if prev_prop == curr_prop and prev_axis == curr_axis:
                        value_change = abs(curr_value - prev_value)
                        time_gap = curr_frame - prev_frame
                        
                        # Consider significant if large value change or long time gap
                        is_significant = (value_change > self.motion_threshold or time_gap > self.time_gap_limit)
                        
                        if is_significant:
                            significant_keyframes.add(curr_frame)
        
        return sorted(significant_keyframes)
    
    def _get_rdp_keyframes(self, bones, fcurves):
        """Ramer-Douglas-Peucker curve simplification - mathematical optimal"""
        rdp_keyframes = set()
        rdp_keyframes.add(self.frame_start)  # Always include start
        rdp_keyframes.add(self.frame_end)    # Always include end
        
        for bone in bones:
            rna_data_stub = f'pose.bones["{bone.name}"]'
            
            # Process each animation channel separately
            for prop in ['location', 'rotation_quaternion', 'rotation_euler', 'scale']:
                prop_sizes = {'location': 3, 'rotation_quaternion': 4, 'rotation_euler': 3, 'scale': 3}
                
                for axis_index in range(prop_sizes[prop]):
                    fcurve = fcurves.find(rna_data_stub + '.' + prop, index=axis_index)
                    if fcurve and len(fcurve.keyframe_points) > 2:
                        # Extract keyframe data as (frame, value) points
                        points = [(kf.co[0], kf.co[1]) for kf in fcurve.keyframe_points]
                        points.sort(key=lambda x: x[0])  # Sort by frame
                        
                        # Apply RDP algorithm to this curve
                        if len(points) > 2:
                            simplified_points = self._rdp_simplify(points, self.rdp_tolerance)
                            
                            # Add frames to keyframe set, respecting min distance
                            for frame, value in simplified_points:
                                rdp_keyframes.add(int(frame))
        
        # Enforce minimum frame distance
        if self.rdp_min_distance > 1:
            rdp_keyframes = self._enforce_min_distance(sorted(rdp_keyframes), self.rdp_min_distance)
        
        return sorted(rdp_keyframes)
    
    def _rdp_simplify(self, points, tolerance):
        """Ramer-Douglas-Peucker algorithm implementation"""
        if len(points) <= 2:
            return points
        
        # Find the point with the maximum distance from line between start and end
        start = points[0]
        end = points[-1]
        max_dist = 0
        max_index = 0
        
        for i in range(1, len(points) - 1):
            dist = self._point_line_distance(points[i], start, end)
            if dist > max_dist:
                max_dist = dist
                max_index = i
        
        # If max distance is greater than tolerance, recursively simplify
        if max_dist > tolerance:
            # Recursively simplify the two segments
            left_segment = self._rdp_simplify(points[:max_index + 1], tolerance)
            right_segment = self._rdp_simplify(points[max_index:], tolerance)
            
            # Combine results (remove duplicate middle point)
            return left_segment[:-1] + right_segment
        else:
            # All points between start and end can be approximated by a straight line
            return [start, end]
    
    def _point_line_distance(self, point, line_start, line_end):
        """Calculate perpendicular distance from point to line"""
        px, py = point
        x1, y1 = line_start
        x2, y2 = line_end
        
        # Calculate the distance from point to line segment
        A = px - x1
        B = py - y1
        C = x2 - x1
        D = y2 - y1
        
        dot = A * C + B * D
        len_sq = C * C + D * D
        
        if len_sq == 0:
            # Line is actually a point
            return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
        
        param = dot / len_sq
        
        if param < 0:
            xx, yy = x1, y1
        elif param > 1:
            xx, yy = x2, y2
        else:
            xx = x1 + param * C
            yy = y1 + param * D
        
        dx = px - xx
        dy = py - yy
        return (dx * dx + dy * dy) ** 0.5
    
    def _enforce_min_distance(self, keyframes, min_distance):
        """Enforce minimum distance between keyframes"""
        if len(keyframes) <= 2:
            return keyframes
        
        filtered = [keyframes[0]]  # Always keep first
        
        for i in range(1, len(keyframes) - 1):
            if keyframes[i] - filtered[-1] >= min_distance:
                filtered.append(keyframes[i])
        
        filtered.append(keyframes[-1])  # Always keep last
        return filtered
    
    def determine_new_keyframe(self, time: float, same_list: list[KeyFrame], current_value: Vector | Quaternion):
        """Determine if a new keyframe should be added
        based on how different the previous frames are from the current frames.
        
        This functuon directly modifies same_list
        """
        prev_keyframe = same_list[-1]
        current_value = current_value.copy()
        a = prev_keyframe.value - current_value
        b = prev_keyframe.slope
        
        #length = 1
        #if isinstance(current_value, Vector):
        #    length = 3
        #elif isinstance(current_value, Quaternion):
        #    length = 4
        #is_mismatch = False
        #for i in range(length):
        #    a_i = a[i]
        #    b_i = b[i]
        #    if 1e-6 < abs(a[i] - b[i]):
        #        is_mismatch = True
        #        break
        
        #is_mismatch = not np.allclose(a, b, atol=1e-6)
        
        diff = a - b
        if isinstance(current_value, Vector):
            is_mismatch = (   diff.x >= 1e-6
                           or diff.y >= 1e-6
                           or diff.z >= 1e-6)
        elif isinstance(current_value, Quaternion):
            is_mismatch = (   diff.x >= 1e-6
                           or diff.y >= 1e-6
                           or diff.z >= 1e-6
                           or diff.w >= 1e-6)
        else:
            is_mismatch = diff >= 1e-6
        
        new_keydict: dict[float, Vector | Quaternion] = {}
        if is_mismatch:
            if len(same_list) >= 2:
                new_keydict[prev_keyframe.time] = prev_keyframe.value.copy()
                # anm_data_raw[bone.name]['LOC'][prev_keyframe.time] = prev_keyframe.value.copy()
            new_keydict[time] = current_value
            # anm_data_raw[bone.name]['LOC'][time] = current_value.copy()
            same_list = [KeyFrame(time, current_value, a)] # update last position and slope
        else:
            # update last position, but not last slope
            if len(same_list) >= 2:
                # Only the first and last elements are ever used, 
                # so just overwrite the last element.
                # This is a HUGE time-save.
                same_list[-1].time = time
                same_list[-1].value = current_value
            else:
                same_list.append(KeyFrame(time, current_value, b.copy())) 
        return same_list, new_keydict
    
    def get_animation_keyframes(self, context, pose, keyed_bones, fcurves):
        fps = context.scene.render.fps
        
        anm_data_raw = {}

        prop_sizes = {'location': 3, 'rotation_quaternion': 4, 'rotation_euler': 3, 'scale': 3}
        
        #class KeyFrame:
        #    def __init__(self, time, value):
        #        self.time = time
        #        self.value = value
        #same_locs = {}
        #same_rots = {}
        #pre_rots = {}
        
        def _convert_loc(pose_bone, loc):
            loc = Vector(loc)
            loc = compat.mul(pose_bone.bone.matrix_local, loc)
            if pose_bone.parent:
                loc = compat.mul(pose_bone.parent.bone.matrix_local.inverted(), loc)
                loc = compat.convert_bl_to_cm_bone_space(loc)
            else:
                loc = compat.convert_bl_to_cm_space(loc)
            return loc * self.scale
        """
        def _convert_quat(pose_bone, quat):
            #quat = Quaternion(quat)
            #'''Can't use matrix transforms here as they would mess up interpolation.'''
            #quat = compat.mul(pose_bone.bone.matrix_local.to_quaternion(), quat)
            
            quat_mat = Quaternion(quat).to_matrix().to_4x4()
            quat_mat = compat.mul(pose_bone.bone.matrix_local, quat_mat)
            #quat = quat_mat.to_quaternion()
            if pose_bone.parent:
                ## inverse of quat.w, quat.x, quat.y, quat.z = quat.w, -quat.z, quat.x, -quat.y
                #quat.w, quat.x, quat.y, quat.z = quat.w, quat.y, -quat.z, -quat.x
                #quat = compat.mul(pose_bone.parent.bone.matrix_local.to_quaternion().inverted(), quat)
                ##quat = compat.mul(pose_bone.parent.bone.matrix_local.inverted().to_quaternion(), quat)\
                quat_mat = compat.convert_bl_to_cm_bone_rotation(quat_mat)
                quat_mat = compat.mul(pose_bone.parent.bone.matrix_local.inverted(), quat_mat)
                quat_mat = compat.convert_bl_to_cm_bone_space(quat_mat)
                quat = quat_mat.to_quaternion()
            else:
                #fix_quat = mathutils.Euler((0, 0, math.radians(-90)), 'XYZ').to_quaternion()
                #fix_quat2 = mathutils.Euler((math.radians(-90), 0, 0), 'XYZ').to_quaternion()
                #quat = compat.mul3(quat, fix_quat, fix_quat2)
                #
                #quat.w, quat.x, quat.y, quat.z = -quat.y, -quat.z, -quat.x, quat.w
                
                #quat.w, quat.x, quat.y, quat.z = quat.w, quat.y, -quat.z, -quat.x
                #quat = compat.mul(mathutils.Matrix.Rotation(math.radians(90.0), 4, 'Z').to_quaternion(), quat)

                quat_mat = compat.convert_bl_to_cm_bone_rotation(quat_mat)
                quat_mat = compat.convert_bl_to_cm_space(quat_mat)
                quat = quat_mat.to_quaternion()
            return quat
        """

        def _convert_quat(pose_bone, quat):
            bone_quat = pose_bone.bone.matrix.to_quaternion()
            quat = Quaternion(quat)

            '''Can't use matrix transforms here as they would mess up interpolation.'''
            quat = compat.mul(bone_quat, quat)
            
            if pose_bone.bone.parent:
                #quat.w, quat.x, quat.y, quat.z = quat.w, -quat.z, quat.x, -quat.y
                quat.w, quat.y, quat.x, quat.z = quat.w, -quat.z, quat.y, -quat.x
            else:
                quat = compat.mul(Matrix.Rotation(math.radians(90.0), 4, 'Z').to_quaternion(), quat)
                quat.w, quat.y, quat.x, quat.z = quat.w, -quat.z, quat.y, -quat.x
            return quat

        for prop, prop_keyed_bones in keyed_bones.items():
            #self.report(type={'INFO'}, message=f_tip_("{prop} {list}", prop=prop, list=prop_keyed_bones))
            for bone_name in prop_keyed_bones:
                if bone_name not in anm_data_raw:
                    anm_data_raw[bone_name] = {}
                    #same_locs[bone_name] = []
                    #same_rots[bone_name] = []
                
                pose_bone = pose.bones[bone_name]
                rna_data_path = f'pose.bones["{bone_name}"].{prop}'
                prop_fcurves = [ fcurves.find(rna_data_path, index=axis_index) 
                                 for axis_index in range(prop_sizes[prop]) ]
                
                # Create missing fcurves, and make existing fcurves CM3D2 compatible.
                for axis_index, fcurve in enumerate(prop_fcurves):
                    if not fcurve:
                        fcurve = fcurves.new(rna_data_path, index=axis_index, action_group=pose_bone.name)
                        prop_fcurves[axis_index] = fcurve
                        self.report(
                            type={'WARNING'}, 
                            message=f_tip_("Creating missing FCurve for {path}[{index}]", 
                                           path=rna_data_path, index=axis_index)
                        )
                    else:
                        override = context.copy()
                        override['active_editable_fcurve'] = fcurve
                        bpy.ops.fcurve.convert_to_cm3d2_interpolation(override, only_selected=False, keep_reports=True)
                        for kwargs in misc_DOPESHEET_MT_editor_menus.REPORTS:
                            self.report(**kwargs)
                        misc_DOPESHEET_MT_editor_menus.REPORTS.clear()


                # Create a list by frame, indicating wether or not there is a keyframe at that time for each fcurve
                is_keyframes = {}
                for fcurve in prop_fcurves:
                    for keyframe in fcurve.keyframe_points:
                        frame = keyframe.co[0]
                        if frame not in is_keyframes:
                            is_keyframes[frame] = [False] * prop_sizes[prop]
                        is_keyframes[frame][fcurve.array_index] = True
                
                # Make sure that no keyframe times are missing any components
                for frame, is_axes in is_keyframes.items():
                    for axis_index, is_axis in enumerate(is_axes):
                        if not is_axis:
                            fcurve = prop_fcurves[axis_index]
                            keyframe = fcurve.keyframe_points.insert(
                                frame         = frame                 , 
                                value         = fcurve.evaluate(frame), 
                                options       = {'NEEDED', 'FAST'}                        
                            )
                            self.report(
                                type={'WARNING'},
                                message=f_tip_("Creating missing keyframe @ frame {frame} for {path}[{index}]",
                                               path=rna_data_path, index=axis_index, frame=frame)
                            )
                
                for fcurve in prop_fcurves:
                    fcurve.update()
                
                for keyframe_index, frame in enumerate(is_keyframes.keys()):
                    time = frame / fps * (1.0 / self.time_scale)

                    _kf = lambda fcurve: fcurve.keyframe_points[keyframe_index]
                    raw_keyframe = [ _kf(fc).co[1] for fc in prop_fcurves ]                                                                            
                    tangent_in   = [ ( _kf(fc).handle_left [1] - _kf(fc).co[1] ) / ( _kf(fc).handle_left [0] - _kf(fc).co[0] ) * fps for fc in prop_fcurves ]
                    tangent_out  = [ ( _kf(fc).handle_right[1] - _kf(fc).co[1] ) / ( _kf(fc).handle_right[0] - _kf(fc).co[0] ) * fps for fc in prop_fcurves ]
                                                   
                    if prop == 'location':
                        if 'LOC' not in anm_data_raw[bone_name]:
                            anm_data_raw[bone_name]['LOC'    ] = {}
                            anm_data_raw[bone_name]['LOC_IN' ] = {}
                            anm_data_raw[bone_name]['LOC_OUT'] = {}
                        anm_data_raw[bone_name]['LOC'    ][time] = _convert_loc(pose_bone, raw_keyframe).copy()
                        anm_data_raw[bone_name]['LOC_IN' ][time] = _convert_loc(pose_bone, tangent_in  ).copy()
                        anm_data_raw[bone_name]['LOC_OUT'][time] = _convert_loc(pose_bone, tangent_out ).copy()
                    elif prop == 'rotation_quaternion':
                        if 'ROT' not in anm_data_raw[bone_name]:
                            anm_data_raw[bone_name]['ROT'    ] = {}
                            anm_data_raw[bone_name]['ROT_IN' ] = {}
                            anm_data_raw[bone_name]['ROT_OUT'] = {}
                        anm_data_raw[bone_name]['ROT'    ][time] = _convert_quat(pose_bone, raw_keyframe).copy()
                        anm_data_raw[bone_name]['ROT_OUT'][time] = _convert_quat(pose_bone, tangent_out ).copy()
                        anm_data_raw[bone_name]['ROT_IN' ][time] = _convert_quat(pose_bone, tangent_in  ).copy()
                        # - - - Alternative Method - - -
                        #raw_keyframe = Quaternion(raw_keyframe)
                        #tangent_in   = Quaternion(tangent_in)
                        #tangent_out  = Quaternion(tangent_out)
                        #converted_quat = _convert_quat(pose_bone, raw_keyframe).copy()
                        #anm_data_raw[bone_name]['ROT'    ][time] = converted_quat.copy()
                        #anm_data_raw[bone_name]['ROT_IN' ][time] = converted_quat.inverted() @ _convert_quat(pose_bone, raw_keyframe @ tangent_in  )
                        #anm_data_raw[bone_name]['ROT_OUT'][time] = converted_quat.inverted() @ _convert_quat(pose_bone, raw_keyframe @ tangent_out )
        
        return anm_data_raw

    def collect_raw_animation_data(self, context, obj: bpy.types.Object, bone_parents):
        arm = obj.data
        pose = obj.pose
        
        copied_action = None
        keyed_bones = None
        has_animation_action = obj.animation_data and obj.animation_data.action
        if has_animation_action:
            if self.export_method == 'KEYED': # This method modifies the action, so copy it.
                copied_action = obj.animation_data.action.copy()
                copied_action.name = obj.animation_data.action.name + "__anm_export"
                fcurves = copied_action.fcurves
            else:
                fcurves = obj.animation_data.action.fcurves
            keyed_bones = self.get_keyed_bones(arm, fcurves)
        elif self.export_method == 'KEYED' or self.export_method == 'DIRECT_OPTIMIZED' or self.is_remove_unkeyed_bone:
            if not has_animation_action:
                raise common.CM3D2ExportError(
                    "Active armature has no animation data / action. Please use \"{method}\" with \"{option}\" disabled, or bake keyframes before exporting.".format(
                        method = "Bake All Frames",
                        option = "Remove Unkeyed Bones"
                    )
                )

        bones = self.clean_bone_list(arm, bone_parents, keyed_bones)

        if self.export_method == 'ALL':
            anm_data_raw = self.get_animation_frames(context, pose, bones, bone_parents)
        elif self.export_method == 'KEYED':
            anm_data_raw = self.get_animation_keyframes(context, pose, keyed_bones, fcurves)
        elif self.export_method == 'DIRECT_OPTIMIZED':
            anm_data_raw = self.get_direct_keyframes_optimized(context, pose, bones, bone_parents, obj.animation_data.action.fcurves)

        if copied_action:
            context.blend_data.actions.remove(copied_action, do_unlink=True, do_id_user=True, do_ui_user=True)
                                   
        return bones, anm_data_raw

    @staticmethod
    def get_bone_parents(arm: bpy.types.Armature, use_armature_property = False) -> dict[str, bpy.types.Bone]:
        bone_parents: dict[str, bpy.types.Bone] = {}
        if use_armature_property:
            for i in range(9999):
                name = "BoneData:" + str(i)
                if name not in arm:
                    continue
                elems = arm[name].split(",")
                if len(elems) != 5:
                    continue
                if elems[0] in arm.bones:
                    if elems[2] in arm.bones:
                        bone_parents[elems[0]] = arm.bones[elems[2]]
                    else:
                        bone_parents[elems[0]] = None
            for bone in arm.bones:
                if bone.name in bone_parents:
                    continue
                bone_parents[bone.name] = bone.parent
        else:
            for bone in arm.bones:
                bone_parents[bone.name] = bone.parent
        return bone_parents
    
    @staticmethod
    def get_keyed_bones(arm: bpy.types.Armature, fcurves):
        keyed_bones = {'location': [], 'rotation_quaternion': [], 'rotation_euler': [], 'scale': []}
        for bone in arm.bones:
            bone: bpy.types.Bone
            rna_data_stub = f'pose.bones["{bone.name}"]'
            for prop, axes in [('location', 3), ('rotation_quaternion', 4), ('rotation_euler', 3), ('scale', 3)]:
                found_fcurve = False
                for axis_index in range(0, axes):
                    if fcurves.find(rna_data_stub + '.' + prop, index=axis_index):
                        found_fcurve = True
                        break
                if found_fcurve:
                    keyed_bones[prop].append(bone.name)
        return keyed_bones

    def clean_bone_list(self, arm, bone_parents, keyed_bones):
        def is_japanese(string):
            for ch in string:
                name = unicodedata.name(ch)
                if 'CJK UNIFIED' in name or 'HIRAGANA' in name or 'KATAKANA' in name:
                    return True
            return False
        
        def is_keyed(bone):
            for prop in keyed_bones:
                if bone.name in keyed_bones[prop]:
                    return True
            return False
        
        def should_remove(bone):
            if self.is_remove_serial_number_bone and common.has_serial_number(bone.name):
                return True
            if self.is_remove_japanese_bone and is_japanese(bone.name):
                return True
            if self.is_remove_unkeyed_bone and not is_keyed(bone):
                return True
            return False
        
        bones = []
        already_bone_names = []
        bones_queue = arm.bones[:]
        while len(bones_queue):
            bone = bones_queue.pop(0)

            if not bone_parents[bone.name]:
                already_bone_names.append(bone.name)
                if should_remove(bone):
                    continue
                if self.is_remove_alone_bone and len(bone.children) == 0:
                    continue
                bones.append(bone)
                continue
            elif bone_parents[bone.name].name in already_bone_names:
                already_bone_names.append(bone.name)
                if should_remove(bone):
                    continue
                if self.is_remove_ik_bone:
                    bone_name_low = bone.name.lower()
                    if '_ik_' in bone_name_low or bone_name_low.endswith('_nub') or bone.name.endswith('Nub'):
                        continue
                bones.append(bone)
                continue


            bones_queue.append(bone)
        return bones
    
    def get_track_data(self, anm_data_raw):
        track_data: dict[str, dict[Anm.ChannelIdType, dict[float, tuple[float, float, float]]]]
        track_data = {}
        for bone_name, channels in anm_data_raw.items():
            track_data[bone_name] = {
                Anm.ChannelIdType.LocalRotationX: {},
                Anm.ChannelIdType.LocalRotationY: {},
                Anm.ChannelIdType.LocalRotationZ: {},
                Anm.ChannelIdType.LocalRotationW: {},
                Anm.ChannelIdType.LocalPositionX: {},
                Anm.ChannelIdType.LocalPositionY: {},
                Anm.ChannelIdType.LocalPositionZ: {},
                Anm.ChannelIdType.ExLocalScaleX : {},
                Anm.ChannelIdType.ExLocalScaleY : {},
                Anm.ChannelIdType.ExLocalScaleZ : {}
            }
            if self.is_location and channels.get('LOC'):
                has_tangents = bool(channels.get('LOC_IN') and channels.get('LOC_OUT'))
                for t, loc in channels['LOC'].items():
                    tangent_in  = channels['LOC_IN' ][t] if has_tangents else Vector()
                    tangent_out = channels['LOC_OUT'][t] if has_tangents else Vector()
                    track_data[bone_name][Anm.ChannelIdType.LocalPositionX][t] = (loc.x, tangent_in.x, tangent_out.x)
                    track_data[bone_name][Anm.ChannelIdType.LocalPositionY][t] = (loc.y, tangent_in.y, tangent_out.y)
                    track_data[bone_name][Anm.ChannelIdType.LocalPositionZ][t] = (loc.z, tangent_in.z, tangent_out.z)
            if self.is_rotation and channels.get('ROT'):
                has_tangents = bool(channels.get('ROT_IN') and channels.get('ROT_OUT'))
                for t, rot in channels['ROT'].items():
                    tangent_in  = channels['ROT_IN' ][t] if has_tangents else Quaternion((0,0,0,0))
                    tangent_out = channels['ROT_OUT'][t] if has_tangents else Quaternion((0,0,0,0))
                    track_data[bone_name][Anm.ChannelIdType.LocalRotationX][t] = (rot.x, tangent_in.x, tangent_out.x)
                    track_data[bone_name][Anm.ChannelIdType.LocalRotationY][t] = (rot.y, tangent_in.y, tangent_out.y)
                    track_data[bone_name][Anm.ChannelIdType.LocalRotationZ][t] = (rot.z, tangent_in.z, tangent_out.z)
                    track_data[bone_name][Anm.ChannelIdType.LocalRotationW][t] = (rot.w, tangent_in.w, tangent_out.w)
            if self.is_scale and channels.get('SCL'):
                has_tangents = bool(channels.get('SCL_IN') and channels.get('SCL_OUT'))
                for t, scl in channels['SCL'].items():
                    tangent_in  = channels['SCL_IN' ][t] if has_tangents else Vector()
                    tangent_out = channels['SCL_OUT'][t] if has_tangents else Vector()
                    track_data[bone_name][Anm.ChannelIdType.ExLocalScaleX][t] = (scl.x, tangent_in.x, tangent_out.x)
                    track_data[bone_name][Anm.ChannelIdType.ExLocalScaleY][t] = (scl.y, tangent_in.y, tangent_out.y)
                    track_data[bone_name][Anm.ChannelIdType.ExLocalScaleZ][t] = (scl.z, tangent_in.z, tangent_out.z)
            
            # Remove empty channels
            track_data[bone_name] = {id: ch for id, ch in track_data[bone_name].items()
                                     if len(ch) > 0}
        return track_data
    
    #@staticmethod
    def assemble_anm(self, bone_parents, bones, track_data, time_step, version=1000, auto_smooth=False) -> Anm:
        ''' Build Anm class from data'''

        anm = Anm()
        # anm.signature = 'CM3D2_ANIM'
        anm.version = version
        
        # Finding generic types can be slow, so do it once here
        PopulateList_Channel_ = PerformanceExtensions.PopulateList[Anm.Channel]
        Array_Keyframe_ = Array[Anm.Keyframe]
        
        bones_with_tracks = [ bone for bone in bones if track_data.get(bone.name) ]
        PerformanceExtensions.PopulateList[Anm.Track](anm.tracks, len(bones_with_tracks))
        for bone, track in zip(bones_with_tracks, anm.tracks):
            track: Anm.Track
            # track.channelId = 1
            
            bone_names = [bone.name]
            current_bone = bone
            while bone_parents[current_bone.name]:
                bone_names.append(bone_parents[current_bone.name].name)
                current_bone = bone_parents[current_bone.name]
            bone_names.reverse()
            
            track.path = '/'.join(bone_names)
            
            PerformanceExtensions.PopulateList[Anm.Channel](
                track.channels, len(track_data[bone.name])
            )
            
            for channel, (channel_id, keyframes) in zip(
                    track.channels,
                    sorted(track_data[bone.name].items(), key=lambda x: x[0])):
                channel: Anm.Channel
                channel.channelId = channel_id
                len_keyframes = len(keyframes)
                channel_keyframes = Array_Keyframe_(len_keyframes)
                channel.keyframes.UnsafeSetArray(channel_keyframes)

                keyframes_list = sorted(keyframes.items(), key=lambda x: x[0])
                for i in range(len(keyframes_list)):
                    keyframe: Anm.Keyframe = channel_keyframes[i]
                    
                    x = keyframes_list[i][0]
                    y, dydx_in, dydx_out = keyframes_list[i][1]

                    keyframe.time = x
                    keyframe.value = y
                    keyframe.inTangent = dydx_in
                    keyframe.outTangent = dydx_out
                    
                    if len(keyframes_list) <= 1:
                        keyframe.inTangent = 0.0
                        keyframe.outTangent = 0.0
                    elif auto_smooth:
                        tan_in, tan_out = AnmBuilder.auto_calc_tangents(time_step, keyframes_list, i)
                        
                        keyframe.inTangent = tan_in
                        keyframe.outTangent = tan_out

                    channel_keyframes[i] = keyframe
                       
        return anm

    @staticmethod
    def auto_calc_tangents(time_step, keyframes_list, i):
        x = keyframes_list[i][0]
        y = keyframes_list[i][1][0]
        
        if i == 0:
            prev_x = x - (keyframes_list[i + 1][0] - x)
            prev_y = y - (keyframes_list[i + 1][1][0] - y)
            next_x = keyframes_list[i + 1][0]
            next_y = keyframes_list[i + 1][1][0]
        elif i == len(keyframes_list) - 1:
            prev_x = keyframes_list[i - 1][0]
            prev_y = keyframes_list[i - 1][1][0]
            next_x = x + (x - keyframes_list[i - 1][0])
            next_y = y + (y - keyframes_list[i - 1][1][0])
        else:
            prev_x = keyframes_list[i - 1][0]
            prev_y = keyframes_list[i - 1][1][0]
            next_x = keyframes_list[i + 1][0]
            next_y = keyframes_list[i + 1][1][0]

        prev_rad = (prev_y - y) / (prev_x - x)
        next_rad = (next_y - y) / (next_x - x)
        join_rad = (prev_rad + next_rad) / 2

        tan_in  = join_rad if x - prev_x <= time_step * 1.5 else prev_rad
        tan_out = join_rad if next_x - x <= time_step * 1.5 else next_rad
        return tan_in,tan_out


    def try_get_bone_inverse(self, bone: bpy.types.PoseBone, frame: float) -> Matrix | None:
        inverse = None
        try:
            inverse = bone.matrix.inverted()
        except ValueError:
            if bone.name not in self._invalid_bones:
                self._invalid_bones[bone.name] = []
            self._invalid_bones[bone.name].append((frame, bone.matrix.copy()))
        return inverse
    
    def report_invalid_bones(self):
        print(self._invalid_bones)
        for bone_name, frames in self._invalid_bones.items():
            self.reporter.report(
                type={'WARNING'},
                message=f_("The bone '{bone}' had an invalid matrix during frames {frame_from} - {frame_to} in animation:\n{matrix}", 
                           bone=bone_name,
                           frame_from=int(frames[0][0]),
                           frame_to=int(frames[-1][0]),
                           matrix=frames[0][1])
            )
        self._invalid_bones = {}


class KeyFrame:
    __slots__ = 'time', 'value', 'slope'
    
    def __init__(self, time, value, slope=None):
        self.time = time
        self.value: Vector | Quaternion = value
        if slope:
            self.slope = slope
        elif type(value) == Vector:
            self.slope = Vector.Fill(len(value))
        elif type(value) == Quaternion:
            self.slope = Quaternion((0,0,0,0))
        else:
            self.slope = 0


# メニューに登録する関数
def menu_func(self, context):
    self.layout.operator(CNV_OT_export_cm3d2_anm.bl_idname, icon_value=common.kiss_icon())
