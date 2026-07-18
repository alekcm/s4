#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

// Builds a small convex mesh from support points. This is used only as a
// generated fallback; the original collider mesh is never assigned to it.
public static class S4LowPolyHullFactory
{
    public static Mesh Build(Vector3[] source, string name, int maxPoints = 40)
    {
        if (source == null || source.Length < 4) return null;
        List<Vector3> p = SelectSupportPoints(source, maxPoints);
        if (p.Count < 4) return null;
        var vertices = new List<Vector3>(p);
        var triangles = new List<int>();
        const float eps = 0.00001f;

        for (int i = 0; i < p.Count - 2; i++)
        for (int j = i + 1; j < p.Count - 1; j++)
        for (int k = j + 1; k < p.Count; k++)
        {
            Vector3 n = Vector3.Cross(p[j] - p[i], p[k] - p[i]);
            float len = n.magnitude;
            if (len < eps) continue;
            n /= len;
            float min = float.PositiveInfinity, max = float.NegativeInfinity;
            for (int q = 0; q < p.Count; q++)
            {
                float d = Vector3.Dot(n, p[q] - p[i]);
                min = Mathf.Min(min, d); max = Mathf.Max(max, d);
            }
            if (min < -eps && max > eps) continue;
            bool flip = max <= eps;
            int a = i, b = flip ? k : j, c = flip ? j : k;
            triangles.Add(a); triangles.Add(b); triangles.Add(c);
        }
        if (triangles.Count < 12) return null;
        var mesh = new Mesh { name = name };
        mesh.indexFormat = vertices.Count > 65535
            ? UnityEngine.Rendering.IndexFormat.UInt32
            : UnityEngine.Rendering.IndexFormat.UInt16;
        mesh.SetVertices(vertices);
        mesh.SetTriangles(triangles, 0, true);
        mesh.RecalculateBounds();
        SaveAsset(mesh);
        return mesh;
    }

    static void SaveAsset(Mesh mesh)
    {
        string dir = Path.Combine(Application.dataPath, "S4Extract_Data", "GeneratedColliderMeshes");
        if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
        AssetDatabase.Refresh();
        string rel = "Assets/" + dir.Substring(Application.dataPath.Length).Replace('\\', '/').TrimStart('/') + "/" + mesh.name + ".asset";
        if (AssetDatabase.LoadAssetAtPath<Mesh>(rel) == null)
            AssetDatabase.CreateAsset(mesh, rel);
    }

    static List<Vector3> SelectSupportPoints(Vector3[] source, int max)
    {
        var result = new List<Vector3>(max);
        var used = new HashSet<int>();
        Vector3[] axes = { Vector3.right, Vector3.left, Vector3.up, Vector3.down, Vector3.forward, Vector3.back };
        foreach (Vector3 axis in axes)
        {
            int best = 0; float value = float.NegativeInfinity;
            for (int i = 0; i < source.Length; i++)
            {
                float v = Vector3.Dot(source[i], axis);
                if (v > value) { value = v; best = i; }
            }
            if (used.Add(best)) result.Add(source[best]);
        }
        while (result.Count < max)
        {
            int best = -1; float bestDistance = float.NegativeInfinity;
            for (int i = 0; i < source.Length; i++)
            {
                if (used.Contains(i)) continue;
                float nearest = float.PositiveInfinity;
                for (int j = 0; j < result.Count; j++)
                    nearest = Mathf.Min(nearest, (source[i] - result[j]).sqrMagnitude);
                if (nearest > bestDistance) { bestDistance = nearest; best = i; }
            }
            if (best < 0) break;
            used.Add(best); result.Add(source[best]);
        }
        return result;
    }
}
#endif
