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
        "    useFileScale: 0\n"
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



def write_breakable_runtime_script(out_root: str) -> tuple[str, str]:
    """Write a per-export runtime component used by the generated BREAKABLE prefab."""
    runtime_dir = os.path.join(out_root, "Runtime")
    os.makedirs(runtime_dir, exist_ok=True)
    class_suffix = hashlib.md5((out_root + "_breakable_runtime").encode("utf-8")).hexdigest()[:10]
    class_name = f"S4BreakablePart_{class_suffix}"
    script_path = os.path.join(runtime_dir, class_name + ".cs")
    cs = f"""// Auto-generated by s4extract.
using UnityEngine;

public class {class_name} : MonoBehaviour
{{
    public GameObject intactRoot;
    public GameObject[] brokenRoots;
    public bool breakOnCollision;
    public float breakImpulseThreshold = 8f;
    public bool isBroken;

    public void Break()
    {{
        if (isBroken) return;
        isBroken = true;
        if (intactRoot != null) intactRoot.SetActive(false);
        if (brokenRoots == null) return;
        foreach (GameObject go in brokenRoots)
        {{
            if (go == null) continue;
            go.SetActive(true);
        }}
    }}

    public void RestoreIntact()
    {{
        isBroken = false;
        if (intactRoot != null) intactRoot.SetActive(true);
        if (brokenRoots == null) return;
        foreach (GameObject go in brokenRoots)
        {{
            if (go == null) continue;
            go.SetActive(false);
        }}
    }}

    void OnCollisionEnter(Collision collision)
    {{
        if (!breakOnCollision || isBroken || collision == null) return;
        if (collision.relativeVelocity.magnitude >= breakImpulseThreshold) Break();
    }}
}}
"""
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(cs)
    return class_name, script_path



def write_editor_material_fixer(out_root: str, pipeline: str,
                                material_texture_pairs: list[tuple[str, str, str | None, str | None]],
                                mesh_names: list[str] | None = None,
                                mesh_material_pairs: list[tuple[str, str]] | None = None,
                                part_asset_material_pairs: list[tuple[str, str]] | None = None,
                                breakable_specs: list[tuple[str, str, list[str]]] | None = None) -> str:
    """Write a Unity Editor script that fixes shaders/textures and assigns material.

    Unity stores package shaders (HDRP/URP) by GUID in .mat files, and those GUIDs
    can differ between SRP/Unity versions. If the GUID is wrong Unity imports the
    material as Hidden/InternalErrorShader (pink). This Editor script runs inside
    the user's project and resolves the shader with Shader.Find(), which is the
    reliable project-local way. It also assigns the first swatch material to scene
    renderers with the exported mesh names (useful if the user drags the FBX,
    not the generated prefab).
    """
    editor_dir = os.path.join(out_root, "Editor")
    os.makedirs(editor_dir, exist_ok=True)
    runtime_breakable_class = "S4BreakablePart"
    class_suffix = hashlib.md5((out_root + pipeline).encode("utf-8")).hexdigest()[:10]
    class_name = f"S4ExtractMaterialFixer_{class_suffix}"
    script_path = os.path.join(editor_dir, class_name + ".cs")

    def esc(s: str | None) -> str:
        if not s:
            return ""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    entries = []
    for mat_name, albedo_file, normal_file, mask_file in material_texture_pairs:
        entries.append(
            f'        new Entry("{esc(mat_name)}", "{esc(os.path.splitext(albedo_file)[0])}", '
            f'"{esc(os.path.splitext(normal_file)[0] if normal_file else "")}", '
            f'"{esc(os.path.splitext(mask_file)[0] if mask_file else "")}"),' )
    entries_src = "\n".join(entries)
    mesh_entries = []
    for mn in (mesh_names or []):
        mesh_entries.append(f'        "{esc(mn)}",')
    mesh_entries_src = "\n".join(mesh_entries)
    mesh_mat_entries = []
    for mn, matn in (mesh_material_pairs or []):
        mesh_mat_entries.append(f'        new MeshMaterial("{esc(mn)}", "{esc(matn)}"),')
    mesh_mat_entries_src = "\n".join(mesh_mat_entries)
    part_entries = []
    for an, matn in (part_asset_material_pairs or []):
        part_entries.append(f'        new PartAsset("{esc(an)}", "{esc(matn)}"),')
    part_entries_src = "\n".join(part_entries)
    break_entries = []
    for intact_name, mat_name, broken_names in (breakable_specs or []):
        arr = ", ".join(f'"{esc(bn)}"' for bn in broken_names)
        break_entries.append(
            f'        new BreakSpec("{esc(intact_name)}", "{esc(mat_name)}", new string[] {{ {arr} }}),')
    break_entries_src = "\n".join(break_entries)
    preferred = pipeline.lower()

    cs = f"""// Auto-generated by s4extract. Safe to delete after materials are fixed.
#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using System.IO;

[InitializeOnLoad]
public static class {class_name}
{{
    struct Entry
    {{
        public string materialName;
        public string albedoName;
        public string normalName;
        public string maskName;
        public Entry(string m, string a, string n, string mask) {{ materialName = m; albedoName = a; normalName = n; maskName = mask; }}
    }}

    struct MeshMaterial
    {{
        public string meshName;
        public string materialName;
        public MeshMaterial(string mesh, string mat) {{ meshName = mesh; materialName = mat; }}
    }}

    struct PartAsset
    {{
        public string assetName;
        public string materialName;
        public PartAsset(string asset, string mat) {{ assetName = asset; materialName = mat; }}
    }}

    struct BreakSpec
    {{
        public string intactAssetName;
        public string materialName;
        public string[] brokenAssetNames;
        public BreakSpec(string intactAsset, string mat, string[] brokenAssets) {{ intactAssetName = intactAsset; materialName = mat; brokenAssetNames = brokenAssets; }}
    }}

    static readonly Entry[] Entries = new Entry[]
    {{
{entries_src}
    }};

    static readonly string[] MeshNames = new string[]
    {{
{mesh_entries_src}
    }};

    static readonly MeshMaterial[] MeshMaterials = new MeshMaterial[]
    {{
{mesh_mat_entries_src}
    }};

    static readonly PartAsset[] PartAssets = new PartAsset[]
    {{
{part_entries_src}
    }};

    static readonly BreakSpec[] BreakSpecs = new BreakSpec[]
    {{
{break_entries_src}
    }};

    static {class_name}()
    {{
        EditorApplication.delayCall += FixAll;
    }}

    [MenuItem("Tools/s4extract/Fix Sims 4 Materials")]
    public static void FixAll()
    {{
        Shader shader = FindBestShader();
        if (shader == null)
        {{
            Debug.LogWarning("s4extract: could not find HDRP/Lit, URP/Lit or Standard shader in this project.");
            return;
        }}

        int changed = 0;
        foreach (Entry e in Entries)
        {{
            Material mat = FindMaterial(e.materialName);
            Texture albedo = FindTexture(e.albedoName);
            Texture normal = FindTexture(e.normalName);
            Texture mask = FindTexture(e.maskName);
            if (mat == null) continue;

            mat.shader = shader;
            if (albedo != null)
            {{
                SetTex(mat, "_BaseColorMap", albedo); // HDRP
                SetTex(mat, "_BaseMap", albedo);      // URP
                SetTex(mat, "_MainTex", albedo);      // Built-in fallback
            }}
            if (normal != null)
            {{
                MakeNormalMap(normal);
                SetTex(mat, "_NormalMap", normal); // HDRP
                SetTex(mat, "_BumpMap", normal);   // URP/Built-in
                mat.EnableKeyword("_NORMALMAP");
                mat.EnableKeyword("_NORMALMAP_TANGENT_SPACE");
            }}
            if (mask != null)
            {{
                SetTex(mat, "_MaskMap", mask);             // HDRP mask map
                SetTex(mat, "_MetallicGlossMap", mask);    // URP/Built-in-ish fallback
            }}
            SetColor(mat, "_BaseColor", Color.white);
            SetColor(mat, "_Color", Color.white);
            SetFloat(mat, "_Metallic", 0f);
            SetFloat(mat, "_Smoothness", 0.5f);
            SetFloat(mat, "_NormalScale", 1f);
            SetFloat(mat, "_BumpScale", 1f);
            SetFloat(mat, "_SurfaceType", 0f); // HDRP opaque
            SetFloat(mat, "_Surface", 0f);     // URP opaque
            mat.renderQueue = -1;
            EditorUtility.SetDirty(mat);
            changed++;
        }}

        int assigned = AssignMappedMaterialsToSceneMeshes();
        string partsPrefab = CreatePartsPrefab();

        if (changed > 0 || assigned > 0 || !string.IsNullOrEmpty(partsPrefab))
        {{
            AssetDatabase.SaveAssets();
            Debug.Log("s4extract: fixed " + changed + " material(s), assigned mapped materials to " + assigned + " renderer(s), parts prefab: " + partsPrefab + ", shader: " + shader.name);
        }}
    }}

    static string CreateReadyPrefab()
    {{
        if (Entries.Length == 0 || MeshNames.Length == 0) return "";
        Material first = FindMaterial(Entries[0].materialName);
        if (first == null) return "";

        string matPath = AssetDatabase.GetAssetPath(first);
        if (string.IsNullOrEmpty(matPath)) return "";
        string folder = Path.GetDirectoryName(matPath).Replace("\\\\", "/");
        string prefabPath = folder + "/{os.path.basename(out_root)}_READY.prefab";

        GameObject root = new GameObject("{os.path.basename(out_root)}_READY");
        int added = 0;
        foreach (string mn in MeshNames)
        {{
            GameObject model = FindModelAsset(mn);
            if (model == null) continue;
            Material target = FindPreferredMaterialForMesh(mn) ?? first;
            GameObject child = (GameObject)PrefabUtility.InstantiatePrefab(model);
            if (child == null) child = Object.Instantiate(model);
            child.name = mn;
            child.transform.SetParent(root.transform, false);
            foreach (Renderer r in child.GetComponentsInChildren<Renderer>(true))
            {{
                Material[] mats = r.sharedMaterials;
                if (mats == null || mats.Length == 0) mats = new Material[] {{ target }};
                for (int i = 0; i < mats.Length; i++) mats[i] = target;
                r.sharedMaterials = mats;
            }}
            added++;
        }}

        if (added == 0)
        {{
            Object.DestroyImmediate(root);
            return "";
        }}

        PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
        Object.DestroyImmediate(root);
        return prefabPath;
    }}

    static string CreatePartsPrefab()
    {{
        if (Entries.Length == 0 || PartAssets.Length == 0) return "";
        Material first = FindMaterial(Entries[0].materialName);
        if (first == null) return "";

        string matPath = AssetDatabase.GetAssetPath(first);
        if (string.IsNullOrEmpty(matPath)) return "";
        string folder = Path.GetDirectoryName(matPath).Replace("\\\\", "/");
        string prefabPath = folder + "/{os.path.basename(out_root)}_PARTS.prefab";

        GameObject root = new GameObject("{os.path.basename(out_root)}_PARTS");
        int added = 0;
        foreach (PartAsset pa in PartAssets)
        {{
            GameObject model = FindModelAsset(pa.assetName);
            if (model == null) continue;
            Material target = FindMaterial(pa.materialName) ?? first;

            GameObject pieceRoot = new GameObject(pa.assetName);
            pieceRoot.transform.SetParent(root.transform, false);
            var breakable = pieceRoot.AddComponent<{runtime_breakable_class}>();
            breakable.breakOnCollision = false;
            breakable.breakImpulseThreshold = 8f;

            GameObject intact = (GameObject)PrefabUtility.InstantiatePrefab(model);
            if (intact == null) intact = Object.Instantiate(model);
            intact.name = "Intact";
            intact.transform.SetParent(pieceRoot.transform, false);
            ApplyMaterialAndPhysics(intact, target, 2f);
            breakable.intactRoot = intact;

            BreakSpec spec;
            var brokenRoots = new System.Collections.Generic.List<GameObject>();
            if (TryGetBreakSpec(pa.assetName, out spec) && spec.brokenAssetNames != null)
            {{
                for (int i = 0; i < spec.brokenAssetNames.Length; i++)
                {{
                    string brokenAssetName = spec.brokenAssetNames[i];
                    GameObject brokenModel = FindModelAsset(brokenAssetName);
                    if (brokenModel == null) continue;
                    GameObject broken = (GameObject)PrefabUtility.InstantiatePrefab(brokenModel);
                    if (broken == null) broken = Object.Instantiate(brokenModel);
                    broken.name = "Broken_" + i;
                    broken.transform.SetParent(pieceRoot.transform, false);
                    ApplyMaterialAndPhysics(broken, target, 1f);
                    broken.SetActive(false);
                    brokenRoots.Add(broken);
                }}
            }}
            breakable.brokenRoots = brokenRoots.ToArray();
            added++;
        }}

        if (added == 0)
        {{
            Object.DestroyImmediate(root);
            return "";
        }}

        PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
        Object.DestroyImmediate(root);
        return prefabPath;
    }}

    static bool TryGetBreakSpec(string intactAssetName, out BreakSpec spec)
    {{
        foreach (BreakSpec bs in BreakSpecs)
        {{
            if (bs.intactAssetName == intactAssetName)
            {{
                spec = bs;
                return true;
            }}
        }}
        spec = default;
        return false;
    }}

    static void ApplyMaterialAndPhysics(GameObject root, Material target, float mass)
    {{
        foreach (Renderer r in root.GetComponentsInChildren<Renderer>(true))
        {{
            Material[] mats = r.sharedMaterials;
            if (mats == null || mats.Length == 0) mats = new Material[] {{ target }};
            for (int i = 0; i < mats.Length; i++) mats[i] = target;
            r.sharedMaterials = mats;
        }}

        Rigidbody rb = root.GetComponent<Rigidbody>();
        if (rb == null) rb = root.AddComponent<Rigidbody>();
        rb.mass = mass;
        rb.drag = 0.05f;
        rb.angularDrag = 0.05f;
        rb.useGravity = true;
        rb.isKinematic = false;
        rb.interpolation = RigidbodyInterpolation.Interpolate;
        rb.collisionDetectionMode = CollisionDetectionMode.ContinuousSpeculative;

        foreach (MeshFilter mf in root.GetComponentsInChildren<MeshFilter>(true))
        {{
            if (mf.sharedMesh == null) continue;
            MeshCollider mc = mf.GetComponent<MeshCollider>();
            if (mc == null) mc = mf.gameObject.AddComponent<MeshCollider>();
            mc.sharedMesh = mf.sharedMesh;
            mc.convex = true;
        }}
    }}

    static string CreateBreakablePrefab()
    {{
        if (Entries.Length == 0 || BreakSpecs.Length == 0) return "";
        Material first = FindMaterial(Entries[0].materialName);
        if (first == null) return "";

        string matPath = AssetDatabase.GetAssetPath(first);
        if (string.IsNullOrEmpty(matPath)) return "";
        string folder = Path.GetDirectoryName(matPath).Replace("\\\\", "/");
        string prefabPath = folder + "/{os.path.basename(out_root)}_BREAKABLE.prefab";

        GameObject root = new GameObject("{os.path.basename(out_root)}_BREAKABLE");
        int added = 0;
        foreach (BreakSpec bs in BreakSpecs)
        {{
            GameObject intactModel = FindModelAsset(bs.intactAssetName);
            if (intactModel == null || bs.brokenAssetNames == null || bs.brokenAssetNames.Length == 0) continue;
            Material target = FindMaterial(bs.materialName) ?? first;

            GameObject pieceRoot = new GameObject(bs.intactAssetName);
            pieceRoot.transform.SetParent(root.transform, false);
            var breakable = pieceRoot.AddComponent<{runtime_breakable_class}>();
            breakable.breakOnCollision = false;
            breakable.breakImpulseThreshold = 8f;

            GameObject intact = (GameObject)PrefabUtility.InstantiatePrefab(intactModel);
            if (intact == null) intact = Object.Instantiate(intactModel);
            intact.name = "Intact";
            intact.transform.SetParent(pieceRoot.transform, false);
            foreach (Renderer r in intact.GetComponentsInChildren<Renderer>(true))
            {{
                Material[] mats = r.sharedMaterials;
                if (mats == null || mats.Length == 0) mats = new Material[] {{ target }};
                for (int i = 0; i < mats.Length; i++) mats[i] = target;
                r.sharedMaterials = mats;
            }}
            Rigidbody intactRb = intact.GetComponent<Rigidbody>();
            if (intactRb == null) intactRb = intact.AddComponent<Rigidbody>();
            intactRb.mass = 2f;
            intactRb.drag = 0.05f;
            intactRb.angularDrag = 0.05f;
            intactRb.useGravity = true;
            intactRb.isKinematic = false;
            intactRb.interpolation = RigidbodyInterpolation.Interpolate;
            intactRb.collisionDetectionMode = CollisionDetectionMode.ContinuousSpeculative;
            foreach (MeshFilter mf in intact.GetComponentsInChildren<MeshFilter>(true))
            {{
                if (mf.sharedMesh == null) continue;
                MeshCollider mc = mf.GetComponent<MeshCollider>();
                if (mc == null) mc = mf.gameObject.AddComponent<MeshCollider>();
                mc.sharedMesh = mf.sharedMesh;
                mc.convex = true;
            }}

            var brokenRoots = new System.Collections.Generic.List<GameObject>();
            foreach (string brokenName in bs.brokenAssetNames)
            {{
                GameObject brokenModel = FindModelAsset(brokenName);
                if (brokenModel == null) continue;
                GameObject broken = (GameObject)PrefabUtility.InstantiatePrefab(brokenModel);
                if (broken == null) broken = Object.Instantiate(brokenModel);
                broken.name = brokenName;
                broken.transform.SetParent(pieceRoot.transform, false);
                foreach (Renderer r in broken.GetComponentsInChildren<Renderer>(true))
                {{
                    Material[] mats = r.sharedMaterials;
                    if (mats == null || mats.Length == 0) mats = new Material[] {{ target }};
                    for (int i = 0; i < mats.Length; i++) mats[i] = target;
                    r.sharedMaterials = mats;
                }}
                Rigidbody rb = broken.GetComponent<Rigidbody>();
                if (rb == null) rb = broken.AddComponent<Rigidbody>();
                rb.mass = 1f;
                rb.drag = 0.05f;
                rb.angularDrag = 0.05f;
                rb.useGravity = true;
                rb.isKinematic = false;
                rb.interpolation = RigidbodyInterpolation.Interpolate;
                rb.collisionDetectionMode = CollisionDetectionMode.ContinuousSpeculative;
                foreach (MeshFilter mf in broken.GetComponentsInChildren<MeshFilter>(true))
                {{
                    if (mf.sharedMesh == null) continue;
                    MeshCollider mc = mf.GetComponent<MeshCollider>();
                    if (mc == null) mc = mf.gameObject.AddComponent<MeshCollider>();
                    mc.sharedMesh = mf.sharedMesh;
                    mc.convex = true;
                }}
                broken.SetActive(false);
                brokenRoots.Add(broken);
            }}

            breakable.intactRoot = intact;
            breakable.brokenRoots = brokenRoots.ToArray();
            added++;
        }}

        if (added == 0)
        {{
            Object.DestroyImmediate(root);
            return "";
        }}

        PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
        Object.DestroyImmediate(root);
        return prefabPath;
    }}

    static int AssignMappedMaterialsToSceneMeshes()
    {{
        if (Entries.Length == 0 || MeshNames.Length == 0) return 0;
        Material first = FindMaterial(Entries[0].materialName);
        if (first == null) return 0;
        int assigned = 0;
#if UNITY_2023_1_OR_NEWER
        Renderer[] renderers = Object.FindObjectsByType<Renderer>(FindObjectsInactive.Include, FindObjectsSortMode.None);
#else
        Renderer[] renderers = Object.FindObjectsOfType<Renderer>(true);
#endif
        foreach (Renderer r in renderers)
        {{
            string goName = r.gameObject.name;
            Material target = FindPreferredMaterialForMesh(goName);
            if (target == null)
            {{
                foreach (string mn in MeshNames)
                {{
                    if (goName == mn || goName.StartsWith(mn))
                    {{
                        target = FindPreferredMaterialForMesh(mn) ?? first;
                        break;
                    }}
                }}
            }}
            if (target == null) continue;
            Material[] mats = r.sharedMaterials;
            if (mats == null || mats.Length == 0) mats = new Material[] {{ target }};
            for (int i = 0; i < mats.Length; i++) mats[i] = target;
            r.sharedMaterials = mats;
            EditorUtility.SetDirty(r);
            assigned++;
        }}
        if (assigned > 0) MarkOpenScenesDirty();
        return assigned;
    }}

    static void MarkOpenScenesDirty()
    {{
        for (int i = 0; i < SceneManager.sceneCount; i++)
        {{
            var scene = SceneManager.GetSceneAt(i);
            if (scene.isLoaded) UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(scene);
        }}
    }}

    static Material FindPreferredMaterialForMesh(string meshOrGoName)
    {{
        if (string.IsNullOrEmpty(meshOrGoName)) return null;
        foreach (MeshMaterial mm in MeshMaterials)
        {{
            if (meshOrGoName == mm.meshName || meshOrGoName.StartsWith(mm.meshName))
            {{
                Material m = FindMaterial(mm.materialName);
                if (m != null) return m;
            }}
        }}
        return null;
    }}

    static Shader FindBestShader()
    {{
        string preferred = "{preferred}";
        if (preferred == "hdrp")
        {{
            Shader s = Shader.Find("HDRP/Lit");
            if (s != null) return s;
        }}
        if (preferred == "urp")
        {{
            Shader s = Shader.Find("Universal Render Pipeline/Lit");
            if (s != null) return s;
        }}
        if (preferred == "builtin")
        {{
            Shader s = Shader.Find("Standard");
            if (s != null) return s;
        }}

        RenderPipelineAsset rp = GraphicsSettings.currentRenderPipeline;
        string rpName = rp != null ? rp.GetType().Name.ToLowerInvariant() : "";
        if (rpName.Contains("hd"))
        {{
            Shader s = Shader.Find("HDRP/Lit");
            if (s != null) return s;
        }}
        if (rpName.Contains("universal") || rpName.Contains("urp"))
        {{
            Shader s = Shader.Find("Universal Render Pipeline/Lit");
            if (s != null) return s;
        }}

        string[] candidates = new string[] {{ "HDRP/Lit", "Universal Render Pipeline/Lit", "Standard" }};
        foreach (string c in candidates)
        {{
            Shader s = Shader.Find(c);
            if (s != null) return s;
        }}
        return null;
    }}

    static Material FindMaterial(string name)
    {{
        if (string.IsNullOrEmpty(name)) return null;
        string[] guids = AssetDatabase.FindAssets(name + " t:Material");
        foreach (string g in guids)
        {{
            string path = AssetDatabase.GUIDToAssetPath(g);
            Material m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m != null && m.name == name) return m;
        }}
        return null;
    }}

    static GameObject FindModelAsset(string name)
    {{
        if (string.IsNullOrEmpty(name)) return null;
        string[] guids = AssetDatabase.FindAssets(name);

        // Prefer OBJ for the auto-created READY prefab: Unity imports OBJ at
        // 1:1 scale and its face UVs are simple/reliable. FBX is still exported
        // beside it for external tools, but Unity's FBX importer may apply a
        // 0.01 file scale depending on FBX unit metadata.
        foreach (string ext in new string[] {{ ".obj", ".fbx" }})
        {{
            foreach (string g in guids)
            {{
                string path = AssetDatabase.GUIDToAssetPath(g);
                if (!path.ToLowerInvariant().EndsWith(ext)) continue;
                GameObject go = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                if (go != null && (go.name == name || Path.GetFileNameWithoutExtension(path) == name)) return go;
            }}
        }}
        return null;
    }}

    static Texture FindTexture(string nameNoExt)
    {{
        if (string.IsNullOrEmpty(nameNoExt)) return null;
        string[] guids = AssetDatabase.FindAssets(nameNoExt + " t:Texture2D");
        foreach (string g in guids)
        {{
            string path = AssetDatabase.GUIDToAssetPath(g);
            Texture t = AssetDatabase.LoadAssetAtPath<Texture>(path);
            if (t != null && t.name == nameNoExt) return t;
        }}
        return null;
    }}

    static void MakeNormalMap(Texture tex)
    {{
        string path = AssetDatabase.GetAssetPath(tex);
        TextureImporter ti = AssetImporter.GetAtPath(path) as TextureImporter;
        if (ti != null && ti.textureType != TextureImporterType.NormalMap)
        {{
            ti.textureType = TextureImporterType.NormalMap;
            ti.SaveAndReimport();
        }}
    }}

    static void SetTex(Material m, string prop, Texture t)
    {{
        if (m.HasProperty(prop)) m.SetTexture(prop, t);
    }}

    static void SetColor(Material m, string prop, Color c)
    {{
        if (m.HasProperty(prop)) m.SetColor(prop, c);
    }}

    static void SetFloat(Material m, string prop, float v)
    {{
        if (m.HasProperty(prop)) m.SetFloat(prop, v);
    }}
}}
#endif
"""
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(cs)
    return script_path
