// Primitive-shape fitters for S4ColliderOptimizer.
//
// Each fitter returns a PrimitiveFit referencing a UNIT mesh from
// S4PrimitiveMeshFactory (r=1 / h=1) plus center, rotation and localScale.
// The caller applies localScale to the child transform so that one mesh can
// describe every size of that shape.
#if UNITY_EDITOR
using System.Collections.Generic;
using UnityEngine;

public static class S4PrimitiveFitters
{
    public enum PrimitiveShape
    {
        None, Cylinder, HemisphereUp, HemisphereDown,
        Cone, TruncatedCone, LowPolySphere
    }

    public class PrimitiveFit
    {
        public PrimitiveShape shape;
        public Vector3 center;   // world (root-local) position of the child
        public Quaternion rotation;
        public Vector3 scale;    // applied to the child transform (unit mesh -> scaled primitive)
        public Mesh mesh;
        public float volume;
        public float fill;
        public readonly HashSet<MeshCollider> sources = new HashSet<MeshCollider>();
    }

    /// <summary>Try every parametric primitive on mc and return the best (highest fill, fill >= minFill).</summary>
    public static PrimitiveFit TryFitBestPrimitive(Transform root, MeshCollider mc, float minFill)
    {
        PrimitiveFit best = null;
        Try(TryFitCylinder(root, mc),        ref best);
        Try(TryFitHemisphere(root, mc, true),  ref best);
        Try(TryFitHemisphere(root, mc, false), ref best);
        Try(TryFitCone(root, mc),            ref best);
        Try(TryFitTruncatedCone(root, mc),   ref best);
        Try(TryFitLowPolySphere(root, mc),   ref best);
        if (best != null && best.fill >= minFill) return best;
        return null;
    }

    static void Try(PrimitiveFit f, ref PrimitiveFit best)
    {
        if (f == null) return;
        if (Finite(f.center) && Finite(f.scale) && !float.IsNaN(f.fill) && !float.IsInfinity(f.fill) && f.fill > 0f)
            if (best == null || f.fill > best.fill) best = f;
    }

    static bool Finite(Vector3 v) =>
        !float.IsNaN(v.x) && !float.IsNaN(v.y) && !float.IsNaN(v.z) &&
        !float.IsInfinity(v.x) && !float.IsInfinity(v.y) && !float.IsInfinity(v.z);

    // ---------------- shared helpers ----------------

    struct PointCloud { public Vector3[] points; public float meshVolume; public Vector3 mean; }

    static bool GetPoints(Transform root, MeshCollider mc, out PointCloud pc)
    {
        pc = default;
        if (root == null || mc == null || mc.sharedMesh == null) return false;
        Mesh mesh = mc.sharedMesh;
        if (!mesh.isReadable) return false;
        Vector3[] v = mesh.vertices;
        int[] t = mesh.triangles;
        if (v == null || v.Length < 4 || t == null || t.Length < 3) return false;
        var pts = new Vector3[v.Length];
        Vector3 mean = Vector3.zero;
        for (int i = 0; i < v.Length; i++)
        {
            pts[i] = root.InverseTransformPoint(mc.transform.TransformPoint(v[i]));
            mean += pts[i];
        }
        mean /= Mathf.Max(1, pts.Length);
        float vol = ConvexMeshVolume(root, mc, v, t);
        if (vol <= 1e-9f) return false;
        pc.points = pts; pc.meshVolume = vol; pc.mean = mean;
        return true;
    }

    static float ConvexMeshVolume(Transform root, MeshCollider mc, Vector3[] vertices, int[] triangles)
    {
        var p = new Vector3[vertices.Length];
        Vector3 c = Vector3.zero;
        for (int i = 0; i < vertices.Length; i++)
        { p[i] = root.InverseTransformPoint(mc.transform.TransformPoint(vertices[i])); c += p[i]; }
        c /= Mathf.Max(1, p.Length);
        double vol = 0.0;
        for (int i = 0; i + 2 < triangles.Length; i += 3)
        {
            int ia = triangles[i], ib = triangles[i+1], ic = triangles[i+2];
            if (ia < 0 || ia >= p.Length || ib < 0 || ib >= p.Length || ic < 0 || ic >= p.Length) continue;
            vol += Mathf.Abs(Vector3.Dot(p[ia]-c, Vector3.Cross(p[ib]-c, p[ic]-c))) / 6.0;
        }
        return (float)vol;
    }

    static readonly Vector3[] Axes = { Vector3.up, Vector3.down, Vector3.right, Vector3.left, Vector3.forward, Vector3.back };

    delegate PrimitiveFit Fitter(PointCloud pc, Quaternion rot);

    static PrimitiveFit TryAllAxes(PointCloud pc, Fitter fn)
    {
        PrimitiveFit best = null;
        for (int i = 0; i < Axes.Length; i++)
        {
            Quaternion rot = Quaternion.FromToRotation(Vector3.up, Axes[i]);
            PrimitiveFit f = fn(pc, rot);
            if (f != null && (best == null || f.fill > best.fill)) best = f;
        }
        return best;
    }

    static Vector3 ToLocal(Quaternion r, Vector3 p) => Quaternion.Inverse(r) * p;
    static Vector3 ToWorld(Quaternion r, Vector3 p) => r * p;

    static int ChooseSides(float size)
    {
        if (size < 0.10f) return 8;
        if (size < 0.35f) return 12;
        return 16;
    }

    /// <summary>Compute min/max along local Y and max radial distance from Y axis, with center on the Y axis at the Y-midpoint.</summary>
    struct AxialFrame { public Vector3 center; public float yMin, yMax, h, maxR; }

    static AxialFrame ComputeAxialFrame(PointCloud pc, Quaternion rot)
    {
        Vector3 sum = Vector3.zero;
        for (int i = 0; i < pc.points.Length; i++) sum += ToLocal(rot, pc.points[i]);
        Vector3 c = sum / pc.points.Length;
        c.x = 0; c.z = 0; // project onto Y axis
        float yMin = float.PositiveInfinity, yMax = float.NegativeInfinity, maxR = 0f;
        for (int i = 0; i < pc.points.Length; i++)
        {
            Vector3 q = ToLocal(rot, pc.points[i]) - c;
            if (q.y < yMin) yMin = q.y;
            if (q.y > yMax) yMax = q.y;
            float r = Mathf.Sqrt(q.x*q.x + q.z*q.z);
            if (r > maxR) maxR = r;
        }
        float mid = (yMin + yMax) * 0.5f;
        c.y += mid;
        AxialFrame f; f.center = c; f.yMin = yMin - mid; f.yMax = yMax - mid; f.h = f.yMax - f.yMin; f.maxR = maxR;
        return f;
    }

    static float MaxRadialInBand(PointCloud pc, Quaternion rot, Vector3 worldC, float yBand, float half)
    {
        Vector3 cLocal = Quaternion.Inverse(rot) * worldC;
        float maxR = 0f;
        for (int i = 0; i < pc.points.Length; i++)
        {
            Vector3 q = ToLocal(rot, pc.points[i]) - cLocal;
            if (Mathf.Abs(q.y - yBand) > half) continue;
            float r = Mathf.Sqrt(q.x*q.x + q.z*q.z);
            if (r > maxR) maxR = r;
        }
        return maxR;
    }

    // ---------------- Cylinder ----------------
    // Unit mesh: r=1, y in [-1..+1] (height 2).  Scale = (R, H/2, R).
    static PrimitiveFit TryFitCylinder(Transform root, MeshCollider mc)
    {
        if (!GetPoints(root, mc, out PointCloud pc)) return null;
        return TryAllAxes(pc, (cloud, rot) =>
        {
            AxialFrame f = ComputeAxialFrame(cloud, rot);
            if (f.maxR <= 1e-5f || f.h <= 1e-5f) return null;
            float R = f.maxR * 1.01f;
            float H = f.h    * 1.01f;
            float vol = Mathf.PI * R * R * H;
            if (vol <= 1e-8f) return null;
            int sides = ChooseSides(Mathf.Max(R*2, H));
            Mesh mesh = S4PrimitiveMeshFactory.Cylinder(sides);
            return new PrimitiveFit
            {
                shape = PrimitiveShape.Cylinder,
                center = ToWorld(rot, f.center),
                rotation = rot,
                scale = new Vector3(R, H * 0.5f, R),
                mesh = mesh,
                volume = vol,
                fill = Mathf.Clamp01(cloud.meshVolume / vol),
                sources = { mc }
            };
        });
    }

    // ---------------- Hemisphere ----------------
    // Unit mesh: flat face at y=0 (center), dome in y∈[0..1] for up / [-1..0] for down.
    // Scale = (R, R, R). Center sits on the flat face.
    static PrimitiveFit TryFitHemisphere(Transform root, MeshCollider mc, bool up)
    {
        if (!GetPoints(root, mc, out PointCloud pc)) return null;
        return TryAllAxes(pc, (cloud, rot) =>
        {
            AxialFrame f = ComputeAxialFrame(cloud, rot);
            // The "flat" end is the one whose radial profile is ~full radius; the
            // "dome" end tapers to ~0.  In our axial frame yMin..yMax are centered
            // around 0.  For up=true the dome is at +Y, flat at -Y.
            float rFlat = MaxRadialInBand(cloud, rot, ToWorld(rot, f.center), f.yMin, 0.20f);
            float rDome = MaxRadialInBand(cloud, rot, ToWorld(rot, f.center), f.yMax, 0.20f);
            float flatR = up ? rFlat : rDome;
            float domeR = up ? rDome : rFlat;
            if (flatR <= 1e-4f) return null;
            float R = Mathf.Max(flatR, f.h, domeR) * 1.02f;
            float vol = (2f/3f) * Mathf.PI * R * R * R;
            if (vol <= 1e-8f) return null;
            // Mesh origin is on the flat face. Our axial center is at y-midpoint,
            // so shift by -h/2 = +f.yMin (negative) toward the flat face.
            Vector3 c = f.center;
            c.y += up ? f.yMin : f.yMax;
            int sides = R > 0.25f ? 12 : 8;
            int rings = 4;
            Mesh mesh = up
                ? S4PrimitiveMeshFactory.Hemisphere(sides, rings)
                : S4PrimitiveMeshFactory.HemisphereDown(sides, rings);
            return new PrimitiveFit
            {
                shape = up ? PrimitiveShape.HemisphereUp : PrimitiveShape.HemisphereDown,
                center = ToWorld(rot, c),
                rotation = rot,
                scale = new Vector3(R, R, R),
                mesh = mesh,
                volume = vol,
                fill = Mathf.Clamp01(cloud.meshVolume / vol),
                sources = { mc }
            };
        });
    }

    // ---------------- Cone ----------------
    // Unit mesh: base at y=0 (r=1), apex at y=1.  Scale = (R, H, R). Center = base center.
    static PrimitiveFit TryFitCone(Transform root, MeshCollider mc)
    {
        if (!GetPoints(root, mc, out PointCloud pc)) return null;
        return TryAllAxes(pc, (cloud, rot) =>
        {
            AxialFrame f = ComputeAxialFrame(cloud, rot);
            if (f.h <= 1e-4f) return null;
            float rBot = MaxRadialInBand(cloud, rot, ToWorld(rot, f.center), f.yMin, 0.25f);
            float rTop = MaxRadialInBand(cloud, rot, ToWorld(rot, f.center), f.yMax, 0.25f);
            bool apexTop = rTop < rBot;
            float baseR = (apexTop ? rBot : rTop) * 1.02f;
            if (baseR <= 1e-4f) return null;
            // Reject if both ends are nearly the same radius (that's a cylinder).
            float otherR = apexTop ? rTop : rBot;
            if (otherR > baseR * 0.35f) return null;
            float H = f.h * 1.02f;
            float vol = (1f/3f) * Mathf.PI * baseR * baseR * H;
            if (vol <= 1e-8f) return null;
            // Unit cone origin is on the base. Our axial center is at midpoint: shift by -h/2 (to base).
            Vector3 c = f.center;
            c.y += apexTop ? f.yMin : f.yMax;
            Vector3 centerW;
            Quaternion appliedRot;
            if (apexTop) { centerW = ToWorld(rot, c); appliedRot = rot; }
            else         { centerW = ToWorld(rot, c); appliedRot = rot * Quaternion.Euler(180f, 0f, 0f); }
            int sides = ChooseSides(Mathf.Max(baseR*2, H));
            Mesh mesh = S4PrimitiveMeshFactory.Cone(sides);
            return new PrimitiveFit
            {
                shape = PrimitiveShape.Cone,
                center = centerW,
                rotation = appliedRot,
                scale = new Vector3(baseR, H, baseR),
                mesh = mesh,
                volume = vol,
                fill = Mathf.Clamp01(cloud.meshVolume / vol),
                sources = { mc }
            };
        });
    }

    // ---------------- Truncated cone ----------------
    // Unit mesh: base at y=0 r=1, top at y=1 r=topR (quantized). Scale = (bottomR, H, bottomR)
    // (top radius is baked into the mesh choice).
    static PrimitiveFit TryFitTruncatedCone(Transform root, MeshCollider mc)
    {
        if (!GetPoints(root, mc, out PointCloud pc)) return null;
        return TryAllAxes(pc, (cloud, rot) =>
        {
            AxialFrame f = ComputeAxialFrame(cloud, rot);
            if (f.h <= 1e-4f) return null;
            float rBot = MaxRadialInBand(cloud, rot, ToWorld(rot, f.center), f.yMin, 0.20f);
            float rTop = MaxRadialInBand(cloud, rot, ToWorld(rot, f.center), f.yMax, 0.20f);
            if (rBot <= 1e-4f || rTop <= 1e-4f) return null;
            rBot *= 1.03f; rTop *= 1.03f;
            float ratio = Mathf.Min(rBot, rTop) / Mathf.Max(rBot, rTop);
            if (ratio >= 0.95f) return null; // essentially a cylinder
            if (ratio < 0.10f)  return null; // essentially a cone — handled by Cone fitter
            bool flipped = rTop > rBot;
            float big = flipped ? rTop : rBot;
            float small = flipped ? rBot : rTop;
            float topR = small / big;           // 0..1 ratio for the unit mesh
            float H = f.h * 1.02f;
            float botR = big;
            // Vol of frustum = pi*h/3 * (R^2 + Rr + r^2)
            float vol = (Mathf.PI * H / 3f) * (botR*botR + botR*(botR*topR) + (botR*topR)*(botR*topR));
            if (vol <= 1e-8f) return null;
            // Unit mesh origin is on the big (base) end at y=0. Axial center is at midpoint: shift.
            Vector3 c = f.center;
            c.y += flipped ? f.yMax : f.yMin;
            Vector3 centerW = ToWorld(rot, c);
            Quaternion appliedRot = flipped ? (rot * Quaternion.Euler(180f, 0f, 0f)) : rot;
            int sides = ChooseSides(Mathf.Max(botR*2, H));
            Mesh mesh = S4PrimitiveMeshFactory.TruncatedCone(topR, sides);
            return new PrimitiveFit
            {
                shape = PrimitiveShape.TruncatedCone,
                center = centerW,
                rotation = appliedRot,
                scale = new Vector3(botR, H, botR),
                mesh = mesh,
                volume = vol,
                fill = Mathf.Clamp01(cloud.meshVolume / vol),
                sources = { mc }
            };
        });
    }

    // ---------------- Low-poly sphere ----------------
    // Unit mesh: r=1 centered at origin.  Scale = (R,R,R). Center at centroid.
    static PrimitiveFit TryFitLowPolySphere(Transform root, MeshCollider mc)
    {
        if (!GetPoints(root, mc, out PointCloud pc)) return null;
        Vector3 c = pc.mean;
        float R = 0f;
        for (int iter = 0; iter < 6; iter++)
        {
            int farI = 0; float d2 = 0f;
            for (int i = 0; i < pc.points.Length; i++) { float d = (pc.points[i]-c).sqrMagnitude; if (d > d2) { d2 = d; farI = i; } }
            Vector3 a = pc.points[farI];
            int farJ = 0; d2 = 0f;
            for (int i = 0; i < pc.points.Length; i++) { float d = (pc.points[i]-a).sqrMagnitude; if (d > d2) { d2 = d; farJ = i; } }
            Vector3 b = pc.points[farJ];
            Vector3 nc = (a + b) * 0.5f; float nR = 0.5f * Mathf.Sqrt((b-a).sqrMagnitude);
            for (int pass = 0; pass < 12; pass++)
            {
                bool grew = false;
                for (int i = 0; i < pc.points.Length; i++)
                {
                    Vector3 d = pc.points[i] - nc; float dist = d.magnitude;
                    if (dist > nR + 1e-5f)
                    {
                        float nR2 = (nR + dist) * 0.5f;
                        Vector3 dir = dist > 1e-8f ? d / dist : Vector3.up;
                        nc = pc.points[i] - dir * nR2;
                        nR = nR2; grew = true;
                    }
                }
                if (!grew) break;
            }
            c = nc; R = nR;
        }
        for (int i = 0; i < pc.points.Length; i++) { float d = (pc.points[i]-c).magnitude; if (d > R) R = d; }
        if (R <= 1e-5f) return null;
        float vol = (4f/3f) * Mathf.PI * R * R * R;
        int seg = R > 0.25f ? 12 : 8;
        int ri  = R > 0.25f ? 6 : 4;
        Mesh mesh = S4PrimitiveMeshFactory.Sphere(seg, ri);
        return new PrimitiveFit
        {
            shape = PrimitiveShape.LowPolySphere,
            center = c,
            rotation = Quaternion.identity,
            scale = new Vector3(R, R, R),
            mesh = mesh,
            volume = vol,
            fill = Mathf.Clamp01(pc.meshVolume / vol),
            sources = { mc }
        };
    }
}
#endif
