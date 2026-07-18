// Parametric primitive meshes for convex MeshColliders — UNIT-SHAPE approach.
//
// Every generated mesh is a "unit" primitive (radius=1, height=1 or 2). All
// actual sizing is done via Transform.localScale on the GameObject that holds
// the MeshCollider, exactly like Unity's built-in Box/Capsule/Sphere colliders.
// This means there is ONE baked mesh per SHAPE TOPOLOGY (e.g. cylinder with 16
// sides, hemisphere with 12 sides/4 rings, etc.) reused across the ENTIRE project
// regardless of how many radii/heights the fitter encounters.  A typical Sims
// project ends up with 8-15 tiny .asset files total in PrimitiveColliders/.
//
// All meshes are centered at the local origin with the symmetry axis pointing
// along +Y (so that the fitter can use Quaternion.FromToRotation(Vector3.up, axis)
// and then scale by (R, H, R) or similar).
//
// Editor-only.  Public API returns a shared Mesh:
//     Mesh m = S4PrimitiveMeshFactory.Cylinder(sides);       // r=1, y in [-1..+1] -> scale (R,H/2,R)
//     Mesh m = S4PrimitiveMeshFactory.Hemisphere(sides,rings);  // flat face y=0, dome y in [0..1] -> scale (R,R,R)
//     Mesh m = S4PrimitiveMeshFactory.Cone(sides);           // base at y=0 r=1, apex y=1 -> scale (R,H,R)
//     Mesh m = S4PrimitiveMeshFactory.TruncatedCone(topR, sides); // base y=0 r=1, top y=1 r=topR
//     Mesh m = S4PrimitiveMeshFactory.Sphere(segments, rings);   // r=1 centered at origin -> scale (R,R,R)
#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public static class S4PrimitiveMeshFactory
{
    const string FolderName = "PrimitiveColliders";
    static readonly Dictionary<string, Mesh> _cache = new Dictionary<string, Mesh>();

    // ---------- Public API (all return unit meshes) ----------------------

    /// <summary>Cylinder r=1, axis Y, y in [-1..1] (total height = 2).</summary>
    public static Mesh Cylinder(int sides = 16)
        => Get($"cyl_s{sides}", () => BuildCylinder(sides));

    /// <summary>Hemisphere, flat face at y=0 (normal -Y), dome at y in [0..1].</summary>
    public static Mesh Hemisphere(int sides = 12, int rings = 4)
        => Get($"hemi_s{sides}_r{rings}", () => BuildHemisphere(sides, rings, up: true));

    /// <summary>Hemisphere flipped: flat face at y=0 (normal +Y), dome at y in [-1..0].</summary>
    public static Mesh HemisphereDown(int sides = 12, int rings = 4)
        => Get($"hemidn_s{sides}_r{rings}", () => BuildHemisphere(sides, rings, up: false));

    /// <summary>Cone: base at y=0, r=1; apex at y=1, r=0.</summary>
    public static Mesh Cone(int sides = 16)
        => Get($"cone_s{sides}", () => BuildCone(sides));

    /// <summary>Truncated cone: base at y=0 r=1; top at y=1 r=<paramref name="topR"/> (0..1).</summary>
    /// <remarks>Quantized to 0.05 to keep asset count tiny.</remarks>
    public static Mesh TruncatedCone(float topR, int sides = 16)
    {
        float q = Mathf.Clamp(Mathf.Round(topR * 20f) / 20f, 0.05f, 0.95f);
        int ti = Mathf.RoundToInt(q * 100f);
        return Get($"tcony_tr{ti:D2}_s{sides}", () => BuildTruncatedCone(sides, q));
    }

    /// <summary>UV-sphere r=1, centered at origin.</summary>
    public static Mesh Sphere(int segments = 12, int rings = 6)
        => Get($"sph_s{segments}_r{rings}", () => BuildUVSphere(segments, rings));

    // ---------- Cache + asset persistence --------------------------------

    delegate Mesh Builder();

    static Mesh Get(string key, Builder build)
    {
        lock (_cache)
        {
            if (_cache.TryGetValue(key, out Mesh cached) && cached != null) return cached;
        }
        Mesh existing = LoadAsset(key);
        if (existing != null)
        {
            lock (_cache) { _cache[key] = existing; }
            return existing;
        }
        Mesh m = build();
        m.name = key;
        SaveAsset(key, m);
        lock (_cache) { _cache[key] = m; }
        return m;
    }

    static Mesh LoadAsset(string key)
    {
        string folder = EnsureFolder();
        string rel = "Assets/" + folder.Substring(Application.dataPath.Length).Replace('\\', '/').TrimStart('/');
        string path = rel + "/" + key + ".asset";
        return AssetDatabase.LoadAssetAtPath<Mesh>(path);
    }

    static void SaveAsset(string key, Mesh m)
    {
        string folder = EnsureFolder();
        string rel = "Assets/" + folder.Substring(Application.dataPath.Length).Replace('\\', '/').TrimStart('/');
        string path = rel + "/" + key + ".asset";
        if (AssetDatabase.LoadAssetAtPath<Mesh>(path) != null) return;
        AssetDatabase.CreateAsset(m, path);
    }

    static string EnsureFolder()
    {
        string root;
        if (Directory.Exists(Path.Combine(Application.dataPath, "S4Extract_Data")))
            root = Path.Combine(Application.dataPath, "S4Extract_Data");
        else
            root = FindDirRecursive(Application.dataPath, "S4Extract_Data")
                   ?? Path.Combine(Application.dataPath, "S4Extract_Data");
        string f = Path.Combine(root, FolderName);
        if (!Directory.Exists(f)) { Directory.CreateDirectory(f); AssetDatabase.Refresh(); }
        return f;
    }

    static string FindDirRecursive(string root, string name)
    {
        try
        {
            foreach (string d in Directory.GetDirectories(root))
            {
                if (Path.GetFileName(d) == name) return d;
                string deeper = FindDirRecursive(d, name);
                if (deeper != null) return deeper;
            }
        }
        catch { /* ignore access-denied folders */ }
        return null;
    }

    // ---------- UNIT primitive builders ----------------------------------
    // All meshes are built at unit size; the fitter scales them via Transform.

    static Mesh BuildCylinder(int sides)
    {
        // y in [-1..1], r=1.  Bottom center = 0, top center = 1, then 2*sides rim verts.
        var v = new List<Vector3>();
        var t = new List<int>();
        int bc = v.Count; v.Add(new Vector3(0, -1, 0));
        int tc = v.Count; v.Add(new Vector3(0,  1, 0));
        int rim = v.Count;
        for (int i = 0; i < sides; i++)
        {
            float a = i * Mathf.PI * 2f / sides;
            v.Add(new Vector3(Mathf.Cos(a), -1, Mathf.Sin(a)));
            v.Add(new Vector3(Mathf.Cos(a),  1, Mathf.Sin(a)));
        }
        for (int i = 0; i < sides; i++)
        {
            int b0 = rim + 2*i;
            int b1 = rim + 2*((i+1)%sides);
            int t0 = b0 + 1;
            int t1 = b1 + 1;
            t.Add(bc); t.Add(b0); t.Add(b1); // bottom
            t.Add(tc); t.Add(t1); t.Add(t0); // top
            t.Add(b0); t.Add(t0); t.Add(t1); // side
            t.Add(b0); t.Add(t1); t.Add(b1);
        }
        return Build(v, t);
    }

    static Mesh BuildHemisphere(int sides, int rings, bool up)
    {
        // Flat face on y=0; dome in y in [0..1] if up=true, [-1..0] if up=false.
        var v = new List<Vector3>();
        var t = new List<int>();
        int cap = v.Count; v.Add(new Vector3(0, 0, 0));
        int rimStart = v.Count;
        for (int i = 0; i < sides; i++)
        {
            float a = i * Mathf.PI * 2f / sides;
            v.Add(new Vector3(Mathf.Cos(a), 0, Mathf.Sin(a)));
        }
        int[] ringOffset = new int[rings + 1];
        ringOffset[0] = rimStart;
        for (int k = 1; k < rings; k++)
        {
            ringOffset[k] = v.Count;
            float phi = k * 0.5f * Mathf.PI / rings;
            float y = Mathf.Sin(phi);                       // 0..1
            float r = Mathf.Cos(phi);                       // 1..0
            for (int i = 0; i < sides; i++)
            {
                float a = i * Mathf.PI * 2f / sides;
                v.Add(new Vector3(Mathf.Cos(a)*r, y, Mathf.Sin(a)*r));
            }
        }
        int pole = v.Count; v.Add(new Vector3(0, 1, 0));
        ringOffset[rings] = pole;
        if (!up)
        {
            // Flip Y on every vertex so the dome points down (cap still at y=0, pole at y=-1).
            for (int i = 0; i < v.Count; i++) { var p = v[i]; p.y = -p.y; v[i] = p; }
        }
        // Quads between ring k and k+1.
        for (int k = 0; k < rings - 1; k++)
        {
            int r0 = ringOffset[k], r1 = ringOffset[k+1];
            for (int i = 0; i < sides; i++)
            {
                int a0 = r0 + i, b0 = r0 + ((i+1)%sides);
                int a1 = r1 + i, b1 = r1 + ((i+1)%sides);
                t.Add(a0); t.Add(a1); t.Add(b1);
                t.Add(a0); t.Add(b1); t.Add(b0);
            }
        }
        // Fan to pole.
        {
            int r0 = ringOffset[rings - 1];
            for (int i = 0; i < sides; i++)
            {
                int a0 = r0 + i, b0 = r0 + ((i+1)%sides);
                t.Add(a0); t.Add(pole); t.Add(b0);
            }
        }
        // Flat cap disc.  When dome is up (y>=0), outward normal is -Y; when down, +Y.
        for (int i = 0; i < sides; i++)
        {
            int a0 = rimStart + i;
            int b0 = rimStart + ((i+1)%sides);
            if (up) { t.Add(cap); t.Add(b0); t.Add(a0); }
            else    { t.Add(cap); t.Add(a0); t.Add(b0); }
        }
        return Build(v, t);
    }

    static Mesh BuildCone(int sides)
    {
        // Base at y=0 r=1, apex at y=1 r=0.
        var v = new List<Vector3>();
        var t = new List<int>();
        int bc = v.Count; v.Add(new Vector3(0, 0, 0));
        int apex = v.Count; v.Add(new Vector3(0, 1, 0));
        int rim = v.Count;
        for (int i = 0; i < sides; i++)
        {
            float a = i * Mathf.PI * 2f / sides;
            v.Add(new Vector3(Mathf.Cos(a), 0, Mathf.Sin(a)));
        }
        for (int i = 0; i < sides; i++)
        {
            int a0 = rim + i;
            int b0 = rim + ((i+1)%sides);
            t.Add(bc); t.Add(a0); t.Add(b0);       // base disc
            t.Add(a0); t.Add(apex); t.Add(b0);     // side fan
        }
        return Build(v, t);
    }

    static Mesh BuildTruncatedCone(int sides, float topR)
    {
        // Base at y=0 r=1; top at y=1 r=topR.
        var v = new List<Vector3>();
        var t = new List<int>();
        int bc = v.Count; v.Add(new Vector3(0, 0, 0));
        int tc = v.Count; v.Add(new Vector3(0, 1, 0));
        int bot = v.Count;
        for (int i = 0; i < sides; i++)
        {
            float a = i * Mathf.PI * 2f / sides;
            v.Add(new Vector3(Mathf.Cos(a), 0, Mathf.Sin(a)));
        }
        int top = v.Count;
        for (int i = 0; i < sides; i++)
        {
            float a = i * Mathf.PI * 2f / sides;
            v.Add(new Vector3(Mathf.Cos(a) * topR, 1, Mathf.Sin(a) * topR));
        }
        for (int i = 0; i < sides; i++)
        {
            int a0 = bot + i;
            int b0 = bot + ((i+1)%sides);
            int a1 = top + i;
            int b1 = top + ((i+1)%sides);
            t.Add(bc); t.Add(a0); t.Add(b0);       // base
            t.Add(tc); t.Add(b1); t.Add(a1);       // top
            t.Add(a0); t.Add(a1); t.Add(b1);       // side
            t.Add(a0); t.Add(b1); t.Add(b0);
        }
        return Build(v, t);
    }

    static Mesh BuildUVSphere(int segs, int rings)
    {
        // r=1 centered at origin, +Y = north pole.
        var v = new List<Vector3>();
        var t = new List<int>();
        int[] ringOffset = new int[rings + 1];
        ringOffset[0] = v.Count; v.Add(new Vector3(0, 1, 0));
        for (int k = 1; k < rings; k++)
        {
            ringOffset[k] = v.Count;
            float phi = Mathf.PI * k / rings;
            float y = -Mathf.Cos(phi);
            float r = Mathf.Sin(phi);
            for (int i = 0; i < segs; i++)
            {
                float a = i * Mathf.PI * 2f / segs;
                v.Add(new Vector3(Mathf.Cos(a)*r, y, Mathf.Sin(a)*r));
            }
        }
        ringOffset[rings] = v.Count; v.Add(new Vector3(0, -1, 0));
        // North cap fan.
        for (int i = 0; i < segs; i++)
        {
            int a0 = ringOffset[1] + i;
            int b0 = ringOffset[1] + ((i+1)%segs);
            t.Add(ringOffset[0]); t.Add(a0); t.Add(b0);
        }
        // Middle quads.
        for (int k = 1; k < rings - 1; k++)
        {
            int r0 = ringOffset[k], r1 = ringOffset[k+1];
            for (int i = 0; i < segs; i++)
            {
                int a0 = r0 + i, b0 = r0 + ((i+1)%segs);
                int a1 = r1 + i, b1 = r1 + ((i+1)%segs);
                t.Add(a0); t.Add(a1); t.Add(b1);
                t.Add(a0); t.Add(b1); t.Add(b0);
            }
        }
        // South cap fan.
        for (int i = 0; i < segs; i++)
        {
            int a0 = ringOffset[rings-1] + i;
            int b0 = ringOffset[rings-1] + ((i+1)%segs);
            t.Add(ringOffset[rings]); t.Add(b0); t.Add(a0);
        }
        return Build(v, t);
    }

    static Mesh Build(List<Vector3> v, List<int> t)
    {
        var m = new Mesh();
        m.SetVertices(v);
        m.SetTriangles(t, 0);
        m.RecalculateNormals();
        m.RecalculateBounds();
        return m;
    }
}
#endif
