"""Generate Unity URP/Lit material assets (.mat) plus .meta GUIDs.

A .mat is a YAML asset. To bind a texture we need its .meta GUID, so we also
emit a .meta for each PNG and reference it in the material.
"""
from __future__ import annotations

import hashlib
import os


def _guid_for(path: str) -> str:
    """Deterministic 32-hex-char GUID derived from the asset path."""
    h = hashlib.md5(path.encode("utf-8")).hexdigest()
    return h[:32]


def write_texture_meta(png_path: str) -> str:
    guid = _guid_for(os.path.basename(png_path))
    meta = f"""fileFormatVersion: 2
guid: {guid}
TextureImporter:
  internalIDToNameTable: []
  externalObjects: {{}}
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    enableMipMap: 1
    sRGBTexture: 1
    linearTexture: 0
  textureType: 0
  textureShape: 1
  textureCompression: 1
  alphaUsage: 1
  alphaIsTransparency: 1
  spriteMode: 0
  wrapU: 0
  wrapV: 0
  nPOTScale: 1
  filterMode: 1
  aniso: 1
  textureSettings:
    serializedVersion: 2
    filterMode: 1
    aniso: 1
  platformSettings:
  - serializedVersion: 3
    buildTarget: DefaultTexturePlatform
    maxTextureSize: 2048
    textureFormat: -1
    textureCompression: 1
  userData:
  assetBundleName:
  assetBundleVariant:
"""
    with open(png_path + ".meta", "w", encoding="utf-8") as f:
        f.write(meta)
    return guid


# Built-in Unity shader GUIDs (stable across versions)
SHADER_GUIDS = {
    "urp": "933532a4fcc9baf4fa0491de14d08ed7",      # Universal Render Pipeline/Lit
    "hdrp": "51240e003e0bf41468f3f72d4a8765d8",      # HDRP/Lit
    "builtin": "0000000000000000f000000000000000",  # Standard (built-in shader id)
}


def write_material(mat_path: str, name: str, pipeline: str = "hdrp",
                   diffuse_png: str | None = None,
                   normal_png: str | None = None,
                   specular_png: str | None = None) -> None:
    """Write a Unity .mat for the given pipeline (hdrp|urp|builtin)."""
    pipeline = pipeline.lower()
    if pipeline == "hdrp":
        _write_hdrp_material(mat_path, name, diffuse_png, normal_png, specular_png)
    elif pipeline == "builtin":
        _write_builtin_material(mat_path, name, diffuse_png, normal_png, specular_png)
    else:
        write_urp_material(mat_path, name, diffuse_png, normal_png, specular_png)


def _tex_block(prop_guid):
    if prop_guid is None:
        return "{fileID: 0}"
    return f"{{fileID: 2800000, guid: {prop_guid}, type: 3}}"


def _write_material_meta(mat_path: str):
    guid = _guid_for(os.path.basename(mat_path))
    with open(mat_path + ".meta", "w", encoding="utf-8") as f:
        f.write(f"fileFormatVersion: 2\nguid: {guid}\nNativeFormatImporter:\n"
                f"  externalObjects: {{}}\n  mainObjectFileID: 2100000\n"
                f"  userData:\n  assetBundleName:\n  assetBundleVariant:\n")


def _write_hdrp_material(mat_path, name, diffuse_png, normal_png, specular_png):
    """HDRP/Lit material. Uses HDRP property names (_BaseColorMap, _NormalMap, _MaskMap)."""
    diff_guid = _guid_for(os.path.basename(diffuse_png)) if diffuse_png else None
    norm_guid = _guid_for(os.path.basename(normal_png)) if normal_png else None
    mask_guid = _guid_for(os.path.basename(specular_png)) if specular_png else None

    mat = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 8
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  m_Shader: {{fileID: 4800000, guid: {SHADER_GUIDS['hdrp']}, type: 3}}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords:
  - _NORMALMAP
  - _NORMALMAP_TANGENT_SPACE
  m_InvalidKeywords: []
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap:
    RenderType: Opaque
  disabledShaderPasses:
  - TransparentDepthPrepass
  - TransparentDepthPostpass
  - TransparentBackface
  - RayTracingPrepass
  m_LockedProperties:
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _BaseColorMap:
        m_Texture: {_tex_block(diff_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - _NormalMap:
        m_Texture: {_tex_block(norm_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - _MaskMap:
        m_Texture: {_tex_block(mask_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Ints: []
    m_Floats:
    - _Smoothness: 0.5
    - _Metallic: 0
    - _NormalScale: 1
    - _AlphaCutoff: 0.5
    - _SurfaceType: 0
    m_Colors:
    - _BaseColor: {{r: 1, g: 1, b: 1, a: 1}}
"""
    with open(mat_path, "w", encoding="utf-8") as f:
        f.write(mat)
    _write_material_meta(mat_path)


def _write_builtin_material(mat_path, name, diffuse_png, normal_png, specular_png):
    diff_guid = _guid_for(os.path.basename(diffuse_png)) if diffuse_png else None
    norm_guid = _guid_for(os.path.basename(normal_png)) if normal_png else None
    mat = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 8
  m_Name: {name}
  m_Shader: {{fileID: 46, guid: 0000000000000000f000000000000000, type: 0}}
  m_ValidKeywords:
  - _NORMALMAP
  m_LightmapFlags: 4
  m_CustomRenderQueue: -1
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {_tex_block(diff_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - _BumpMap:
        m_Texture: {_tex_block(norm_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Ints: []
    m_Floats:
    - _Glossiness: 0.5
    - _Metallic: 0
    m_Colors:
    - _Color: {{r: 1, g: 1, b: 1, a: 1}}
"""
    with open(mat_path, "w", encoding="utf-8") as f:
        f.write(mat)
    _write_material_meta(mat_path)


def write_urp_material(mat_path: str, name: str,
                       diffuse_png: str | None = None,
                       normal_png: str | None = None,
                       specular_png: str | None = None) -> None:
    """Write a Unity URP/Lit .mat referencing the given textures (by basename)."""

    def tex_block(prop_guid):
        if prop_guid is None:
            return "{fileID: 0}"
        return f"{{fileID: 2800000, guid: {prop_guid}, type: 3}}"

    diff_guid = _guid_for(os.path.basename(diffuse_png)) if diffuse_png else None
    norm_guid = _guid_for(os.path.basename(normal_png)) if normal_png else None
    spec_guid = _guid_for(os.path.basename(specular_png)) if specular_png else None

    mat = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 8
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  m_Shader: {{fileID: 4800000, guid: 933532a4fcc9baf4fa0491de14d08ed7, type: 3}}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords:
  - _NORMALMAP
  m_InvalidKeywords: []
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap:
    RenderType: Opaque
  disabledShaderPasses: []
  m_LockedProperties:
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _BaseMap:
        m_Texture: {tex_block(diff_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - _BumpMap:
        m_Texture: {tex_block(norm_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - _MetallicGlossMap:
        m_Texture: {tex_block(spec_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    - _MainTex:
        m_Texture: {tex_block(diff_guid)}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Ints: []
    m_Floats:
    - _Smoothness: 0.5
    - _Metallic: 0
    - _BumpScale: 1
    - _Cutoff: 0.5
    - _Surface: 0
    m_Colors:
    - _BaseColor: {{r: 1, g: 1, b: 1, a: 1}}
    - _Color: {{r: 1, g: 1, b: 1, a: 1}}
"""
    with open(mat_path, "w", encoding="utf-8") as f:
        f.write(mat)

    # .meta for the material itself
    guid = _guid_for(os.path.basename(mat_path))
    with open(mat_path + ".meta", "w", encoding="utf-8") as f:
        f.write(f"fileFormatVersion: 2\nguid: {guid}\nNativeFormatImporter:\n"
                f"  externalObjects: {{}}\n  mainObjectFileID: 2100000\n"
                f"  userData:\n  assetBundleName:\n  assetBundleVariant:\n")


# ---------------------------------------------------------------------------
# FBX model importer .meta (so the prefab can reference the model by GUID)
# ---------------------------------------------------------------------------
def write_fbx_meta(fbx_path: str, material_guid: str | None = None) -> str:
    """Write a ModelImporter .meta for an FBX. Returns the model GUID."""
    guid = _guid_for(os.path.basename(fbx_path))
    mat_remap = ""
    if material_guid:
        mat_remap = (
            "    - first:\n"
            "        type: UnityEngine:Material\n"
            "        assembly: UnityEngine.CoreModule\n"
            "        name: " + os.path.splitext(os.path.basename(fbx_path))[0] + "\n"
            "      second: {fileID: 2100000, guid: " + material_guid + ", type: 2}\n"
        )
    meta = (
        "fileFormatVersion: 2\n"
        f"guid: {guid}\n"
        "ModelImporter:\n"
        "  serializedVersion: 22200\n"
        "  internalIDToNameTable: []\n"
        "  externalObjects:\n" + (mat_remap if mat_remap else " {}\n") +
        "  materials:\n"
        "    materialImportMode: 2\n"
        "    materialName: 0\n"
        "    materialSearch: 1\n"
        "    materialLocation: 1\n"
        "  meshes:\n"
        "    useFileScale: 1\n"
        "    globalScale: 1\n"
        "    addColliders: 0\n"
        "    importBlendShapes: 0\n"
        "    keepQuads: 0\n"
        "    optimizeMeshForGPU: 1\n"
        "    weldVertices: 1\n"
        "  importAnimation: 0\n"
        "  animationType: 0\n"
        "  userData:\n"
        "  assetBundleName:\n"
        "  assetBundleVariant:\n"
    )
    with open(fbx_path + ".meta", "w", encoding="utf-8") as f:
        f.write(meta)
    return guid


# ---------------------------------------------------------------------------
# Collider mesh export: one .obj per convex part + .meta so Unity imports each
# as a Mesh asset that a MeshCollider (convex) can reference.
# ---------------------------------------------------------------------------
def write_collider_obj(obj_path: str, part) -> str:
    """Write a single convex part as an .obj and its ModelImporter .meta.

    Returns the GUID. The first imported Mesh in an .obj has fileID 4300000.
    """
    lines = [f"o collider"]
    for (x, y, z) in part.vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for (a, b, c) in part.faces:
        lines.append(f"f {a + 1} {b + 1} {c + 1}")
    with open(obj_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    guid = _guid_for(os.path.basename(obj_path))
    meta = (
        "fileFormatVersion: 2\n"
        f"guid: {guid}\n"
        "ModelImporter:\n"
        "  serializedVersion: 22200\n"
        "  internalIDToNameTable: []\n"
        "  externalObjects: {}\n"
        "  materials:\n"
        "    materialImportMode: 0\n"
        "  meshes:\n"
        "    useFileScale: 1\n"
        "    globalScale: 1\n"
        "    addColliders: 0\n"
        "    importBlendShapes: 0\n"
        "    optimizeMeshForGPU: 0\n"
        "    weldVertices: 1\n"
        "  importAnimation: 0\n"
        "  animationType: 0\n"
        "  userData:\n"
        "  assetBundleName:\n"
        "  assetBundleVariant:\n"
    )
    with open(obj_path + ".meta", "w", encoding="utf-8") as f:
        f.write(meta)
    return guid


# ---------------------------------------------------------------------------
# Prefab generator (compound collider, Unity-canonical):
#   Root GameObject  -> MeshFilter + MeshRenderer (visual FBX mesh) + Rigidbody
#     child "Collider_0" -> MeshCollider(convex) referencing collider OBJ #0
#     child "Collider_1" -> ...
# PhysX merges child colliders into a single compound under the root Rigidbody.
# ---------------------------------------------------------------------------
def write_prefab(prefab_path: str, name: str,
                 fbx_guid: str,
                 material_guid: str | None,
                 colliders,
                 collider_guids: list[str],
                 dynamic: bool = True) -> None:
    guid = _guid_for(os.path.basename(prefab_path))

    # Stable fileIDs
    root_go = 100000
    root_tf = 100001
    mf = 100002
    mr = 100003
    rb = 100004

    L = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"]

    child_go_ids = []
    child_tf_ids = []
    for i in range(len(collider_guids)):
        child_go_ids.append(200000 + i * 10)
        child_tf_ids.append(200001 + i * 10)

    # ---- Root GameObject ----
    L.append(f"--- !u!1 &{root_go}")
    L.append("GameObject:")
    L.append("  m_ObjectHideFlags: 0")
    L.append("  serializedVersion: 6")
    L.append("  m_Component:")
    L.append(f"  - component: {{fileID: {root_tf}}}")
    L.append(f"  - component: {{fileID: {mf}}}")
    L.append(f"  - component: {{fileID: {mr}}}")
    if dynamic:
        L.append(f"  - component: {{fileID: {rb}}}")
    L.append("  m_Layer: 0")
    L.append(f"  m_Name: {name}")
    L.append("  m_TagString: Untagged")
    L.append("  m_IsActive: 1")

    # ---- Root Transform ----
    L.append(f"--- !u!4 &{root_tf}")
    L.append("Transform:")
    L.append(f"  m_GameObject: {{fileID: {root_go}}}")
    L.append("  serializedVersion: 2")
    L.append("  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}")
    L.append("  m_LocalPosition: {x: 0, y: 0, z: 0}")
    L.append("  m_LocalScale: {x: 1, y: 1, z: 1}")
    L.append("  m_Children:")
    for tf in child_tf_ids:
        L.append(f"  - {{fileID: {tf}}}")
    if not child_tf_ids:
        L[-1] = "  m_Children: []"
    L.append("  m_Father: {fileID: 0}")

    # ---- MeshFilter (visual mesh from FBX) ----
    L.append(f"--- !u!33 &{mf}")
    L.append("MeshFilter:")
    L.append(f"  m_GameObject: {{fileID: {root_go}}}")
    L.append(f"  m_Mesh: {{fileID: 4300000, guid: {fbx_guid}, type: 3}}")

    # ---- MeshRenderer ----
    L.append(f"--- !u!23 &{mr}")
    L.append("MeshRenderer:")
    L.append(f"  m_GameObject: {{fileID: {root_go}}}")
    L.append("  m_Enabled: 1")
    L.append("  m_CastShadows: 1")
    L.append("  m_ReceiveShadows: 1")
    L.append("  m_Materials:")
    if material_guid:
        L.append(f"  - {{fileID: 2100000, guid: {material_guid}, type: 2}}")
    else:
        L.append("  - {fileID: 0}")

    # ---- Rigidbody ----
    if dynamic:
        L.append(f"--- !u!54 &{rb}")
        L.append("Rigidbody:")
        L.append(f"  m_GameObject: {{fileID: {root_go}}}")
        L.append("  serializedVersion: 4")
        L.append("  m_Mass: 10")
        L.append("  m_Drag: 0.05")
        L.append("  m_AngularDrag: 0.05")
        L.append("  m_UseGravity: 1")
        L.append("  m_IsKinematic: 0")
        L.append("  m_Interpolate: 1")
        L.append("  m_Constraints: 0")
        L.append("  m_CollisionDetection: 1")

    # ---- Child collider objects (compound) ----
    for i, cguid in enumerate(collider_guids):
        cgo = child_go_ids[i]
        ctf = child_tf_ids[i]
        cmc = 200002 + i * 10
        L.append(f"--- !u!1 &{cgo}")
        L.append("GameObject:")
        L.append("  m_ObjectHideFlags: 0")
        L.append("  serializedVersion: 6")
        L.append("  m_Component:")
        L.append(f"  - component: {{fileID: {ctf}}}")
        L.append(f"  - component: {{fileID: {cmc}}}")
        L.append("  m_Layer: 0")
        L.append(f"  m_Name: Collider_{i}")
        L.append("  m_TagString: Untagged")
        L.append("  m_IsActive: 1")

        L.append(f"--- !u!4 &{ctf}")
        L.append("Transform:")
        L.append(f"  m_GameObject: {{fileID: {cgo}}}")
        L.append("  serializedVersion: 2")
        L.append("  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}")
        L.append("  m_LocalPosition: {x: 0, y: 0, z: 0}")
        L.append("  m_LocalScale: {x: 1, y: 1, z: 1}")
        L.append("  m_Children: []")
        L.append(f"  m_Father: {{fileID: {root_tf}}}")

        L.append(f"--- !u!64 &{cmc}")
        L.append("MeshCollider:")
        L.append(f"  m_GameObject: {{fileID: {cgo}}}")
        L.append("  m_Material: {fileID: 0}")
        L.append("  m_IsTrigger: 0")
        L.append("  m_Enabled: 1")
        L.append("  serializedVersion: 4")
        L.append("  m_Convex: 1")
        L.append("  m_CookingOptions: 30")
        L.append(f"  m_Mesh: {{fileID: 4300000, guid: {cguid}, type: 3}}")

    # If no convex parts, add a single Box collider from bbox on the root.
    if not collider_guids:
        mn = colliders.bbox_min if colliders else (0, 0, 0)
        mx = colliders.bbox_max if colliders else (1, 1, 1)
        cx, cy, cz = (mn[0]+mx[0])/2, (mn[1]+mx[1])/2, (mn[2]+mx[2])/2
        sx = max(mx[0]-mn[0], 1e-4); sy = max(mx[1]-mn[1], 1e-4); sz = max(mx[2]-mn[2], 1e-4)
        bc = 100005
        # add component ref to root
        # (rewrite root component list to include the box collider)
        for j, line in enumerate(L):
            if line == f"--- !u!1 &{root_go}":
                # find component list end and insert
                k = j
                while not L[k].startswith("  m_Layer:"):
                    k += 1
                L.insert(k, f"  - component: {{fileID: {bc}}}")
                break
        L.append(f"--- !u!65 &{bc}")
        L.append("BoxCollider:")
        L.append(f"  m_GameObject: {{fileID: {root_go}}}")
        L.append("  m_IsTrigger: 0")
        L.append("  m_Enabled: 1")
        L.append("  serializedVersion: 2")
        L.append(f"  m_Size: {{x: {sx:.6f}, y: {sy:.6f}, z: {sz:.6f}}}")
        L.append(f"  m_Center: {{x: {cx:.6f}, y: {cy:.6f}, z: {cz:.6f}}}")

    with open(prefab_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(prefab_path + ".meta", "w", encoding="utf-8") as f:
        f.write(f"fileFormatVersion: 2\nguid: {guid}\n"
                f"PrefabImporter:\n  externalObjects: {{}}\n  userData:\n"
                f"  assetBundleName:\n  assetBundleVariant:\n")
