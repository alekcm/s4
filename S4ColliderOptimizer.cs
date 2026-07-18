#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

/// <summary>
/// Offline editor tool for replacing simple convex MeshColliders with oriented
/// BoxColliders and merging aligned neighbouring boxes. Put this file anywhere
/// under Assets/Editor, select a prefab instance/root, then open
/// Tools/S4 Extract/Collider Optimizer.
/// </summary>
public sealed class S4ColliderOptimizer : EditorWindow
{
    const string GeneratedRootName = "__S4_OptimizedColliders";
    const string DisabledMarkerPrefix = "__S4_DisabledMeshCollider_";

    [SerializeField, Range(0.10f, 0.99f)] float minBoxFill = 0.50f;
    [SerializeField, Range(0.10f, 0.99f)] float minCapsuleFill = 0.30f;
    [SerializeField, Range(1.2f, 8f)] float minCapsuleElongation = 2.2f;
    [SerializeField, Range(1f, 3f)] float maxCapsuleRadialAspect = 1.40f;
    [SerializeField] bool preferSmoothCapsules = true;
    [SerializeField, Range(0f, 0.25f)] float maxMergeInflation = 0.04f;
    [SerializeField] float contactEpsilon = 0.003f;
    [SerializeField, Range(0f, 20f)] float maxAxisAngle = 6f;
    [SerializeField] bool includeInactive = false;

    [MenuItem("Tools/S4 Extract/Collider Optimizer")]
    static void Open() => GetWindow<S4ColliderOptimizer>("S4 Collider Optimizer");

    /// <summary>Automatic, destructive mode used by Fix All Exports.
    /// Replaced MeshColliders are removed because the READY prefab can always
    /// be regenerated from export data.</summary>
    public static string OptimizeForBatch(GameObject root,
        float minimumBoxFill = 0.50f, float mergeEmptyVolume = 0.04f,
        float gapEpsilon = 0.003f, float axisAngle = 6f)
    {
        if (root == null) return "S4 collider optimizer: null root";
        var worker = CreateInstance<S4ColliderOptimizer>();
        try
        {
            worker.minBoxFill = minimumBoxFill;
            // Batch mode must be substantially more conservative than the
            // manual preview tool. A false positive creates invisible shelves.
            worker.minCapsuleFill = 0.55f;
            worker.minCapsuleElongation = 2.5f;
            worker.maxCapsuleRadialAspect = 1.25f;
            worker.preferSmoothCapsules = true;
            worker.maxMergeInflation = mergeEmptyVolume;
            worker.contactEpsilon = gapEpsilon;
            worker.maxAxisAngle = axisAngle;
            worker.includeInactive = true;
            string result = worker.OptimizeRoot(root, false);

            // Batch-created READY prefabs are reproducible. Keeping disabled
            // MeshCollider components would waste serialized data and memory.
            foreach (MeshCollider mc in root.GetComponentsInChildren<MeshCollider>(true))
                if (!mc.enabled) DestroyImmediate(mc);
            foreach (Transform t in root.GetComponentsInChildren<Transform>(true)
                         .Where(t => t != root.transform &&
                                     t.name.StartsWith(DisabledMarkerPrefix, StringComparison.Ordinal))
                         .ToArray())
                DestroyImmediate(t.gameObject);
            return result;
        }
        finally { DestroyImmediate(worker); }
    }

    void OnGUI()
    {
        EditorGUILayout.HelpBox(
            "Replaces simple convex MeshColliders with oriented boxes or smooth capsules, " +
            "then merges aligned touching boxes. Originals are disabled, not deleted.",
            MessageType.Info);
        minBoxFill = EditorGUILayout.Slider(
            new GUIContent("Minimum box fill", "Mesh volume / fitted box volume. Curved Sims furniture often needs 0.40–0.60."),
            minBoxFill, 0.10f, 0.99f);
        EditorGUILayout.BeginHorizontal();
        if (GUILayout.Button("Safe 0.70")) minBoxFill = 0.70f;
        if (GUILayout.Button("Furniture 0.50")) minBoxFill = 0.50f;
        if (GUILayout.Button("Aggressive 0.35")) minBoxFill = 0.35f;
        EditorGUILayout.EndHorizontal();
        EditorGUILayout.Space(3);
        preferSmoothCapsules = EditorGUILayout.Toggle(
            new GUIContent("Prefer smooth capsules", "Use capsules for long parts with a near-round cross-section."),
            preferSmoothCapsules);
        minCapsuleFill = EditorGUILayout.Slider("Minimum capsule fill", minCapsuleFill, 0.10f, 0.99f);
        minCapsuleElongation = EditorGUILayout.Slider("Capsule elongation", minCapsuleElongation, 1.2f, 8f);
        maxCapsuleRadialAspect = EditorGUILayout.Slider("Capsule radial aspect", maxCapsuleRadialAspect, 1f, 3f);
        maxMergeInflation = EditorGUILayout.Slider(
            new GUIContent("Merge empty volume", "Maximum empty fraction introduced by merging boxes."),
            maxMergeInflation, 0f, 0.25f);
        contactEpsilon = EditorGUILayout.FloatField(
            new GUIContent("Contact epsilon", "Allowed gap in root-local Unity units."),
            contactEpsilon);
        maxAxisAngle = EditorGUILayout.Slider("Maximum axis angle", maxAxisAngle, 0f, 20f);
        includeInactive = EditorGUILayout.Toggle("Include inactive", includeInactive);

        EditorGUILayout.Space();
        using (new EditorGUI.DisabledScope(Selection.activeGameObject == null))
        {
            if (GUILayout.Button("Analyze selected", GUILayout.Height(28))) AnalyzeSelected();
            if (GUILayout.Button("Optimize selected (Undo supported)", GUILayout.Height(34))) OptimizeSelected();
            if (GUILayout.Button("Restore originals", GUILayout.Height(24))) RestoreSelected();
        }
        EditorGUILayout.HelpBox(
            "For fragmented/curved Sims furniture start with fill 0.50. Use 0.70 for straight parts " +
            "or 0.35 only after visual inspection. Merge empty volume 0.02–0.05 is conservative.",
            MessageType.None);
    }

    GameObject SelectedRoot()
    {
        GameObject root = Selection.activeGameObject;
        if (root == null) Debug.LogWarning("Select a prefab instance or object root first.");
        return root;
    }

    List<MeshCollider> SourceColliders(GameObject root, bool enabledOnly)
    {
        return root.GetComponentsInChildren<MeshCollider>(includeInactive)
            .Where(c => c != null && c.sharedMesh != null && c.convex &&
                        !c.sharedMesh.name.Contains("parametric_keep") &&
                        c.transform.name != GeneratedRootName &&
                        c.transform.parent?.name != GeneratedRootName &&
                        (!enabledOnly || c.enabled) &&
                        !IsUnderGeneratedRoot(c.transform, root.transform))
            .ToList();
    }

    static bool IsUnderGeneratedRoot(Transform t, Transform stop)
    {
        while (t != null && t != stop)
        {
            if (t.name == GeneratedRootName) return true;
            t = t.parent;
        }
        return false;
    }

    static bool IsPrefabAsset(GameObject go, out string path)
    {
        path = go != null ? AssetDatabase.GetAssetPath(go) : null;
        return go != null && !string.IsNullOrEmpty(path) &&
               PrefabUtility.IsPartOfPrefabAsset(go);
    }

    void AnalyzeSelected()
    {
        GameObject selected = SelectedRoot();
        if (selected == null) return;
        if (IsPrefabAsset(selected, out string path))
        {
            GameObject contents = PrefabUtility.LoadPrefabContents(path);
            try { AnalyzeRoot(contents, null); }
            finally { PrefabUtility.UnloadPrefabContents(contents); }
        }
        else AnalyzeRoot(selected, selected);
    }

    void AnalyzeRoot(GameObject root, UnityEngine.Object logContext)
    {
        List<MeshCollider> colliders = SourceColliders(root, true);
        var fills = new List<float>();
        int capsuleCandidates = 0;
        foreach (MeshCollider mc in colliders)
        {
            if (TryFitBox(root.transform, mc, out BoxFit fit)) fills.Add(fit.fill);
            if (preferSmoothCapsules && TryFitCapsule(root.transform, mc, out CapsuleFit capsule) &&
                IsCapsuleCandidate(capsule)) capsuleCandidates++;
        }
        fills.Sort();
        int boxable = fills.Count(v => v >= minBoxFill);
        int at35 = fills.Count(v => v >= 0.35f);
        int at50 = fills.Count(v => v >= 0.50f);
        int at70 = fills.Count(v => v >= 0.70f);
        float best = fills.Count > 0 ? fills[fills.Count - 1] : 0f;
        float average = fills.Count > 0 ? fills.Average() : 0f;
        string message = $"[S4 collider optimizer] {root.name}: {colliders.Count} enabled convex " +
                         $"MeshColliders; {boxable} candidates at current fill >= {minBoxFill:F2}. " +
                         $"Successfully analyzed {fills.Count}; average {average:F3}, best {best:F3}. " +
                         $"Candidates by preset: aggressive(0.35)={at35}, furniture(0.50)={at50}, safe(0.70)={at70}; " +
                         $"smooth capsule candidates={capsuleCandidates}.";
        if (logContext != null) Debug.Log(message, logContext); else Debug.Log(message);
        ShowNotification(new GUIContent(
            $"boxes {boxable}, capsules {capsuleCandidates}; best box {best:F2}"));
    }

    void OptimizeSelected()
    {
        GameObject selected = SelectedRoot();
        if (selected == null) return;
        if (IsPrefabAsset(selected, out string path))
        {
            GameObject contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                string result = OptimizeRoot(contents, false);
                PrefabUtility.SaveAsPrefabAsset(contents, path);
                AssetDatabase.SaveAssets();
                Debug.Log(result + $" Saved prefab asset: {path}");
                ShowNotification(new GUIContent("Prefab optimized and saved"));
            }
            finally { PrefabUtility.UnloadPrefabContents(contents); }
            return;
        }
        string sceneResult = OptimizeRoot(selected, true);
        Debug.Log(sceneResult, selected);
        ShowNotification(new GUIContent("Selected object optimized"));
    }

    string OptimizeRoot(GameObject root, bool useUndo)
    {
        int undoGroup = -1;
        if (useUndo)
        {
            Undo.IncrementCurrentGroup();
            undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Optimize S4 colliders");
        }

        RestoreInternal(root, useUndo);
        List<MeshCollider> originals = SourceColliders(root, true);
        var boxes = new List<BoxFit>();
        var capsules = new List<CapsuleFit>();
        foreach (MeshCollider mc in originals)
        {
            CapsuleFit capsule = null;
            bool capsuleChosen = preferSmoothCapsules &&
                TryFitCapsule(root.transform, mc, out capsule) &&
                IsCapsuleCandidate(capsule);
            if (capsuleChosen)
                capsules.Add(capsule);
            else if (TryFitBox(root.transform, mc, out BoxFit fit) && fit.fill >= minBoxFill)
                boxes.Add(fit);
        }

        int beforeMerge = boxes.Count;
        boxes = MergeBoxes(boxes);
        if (boxes.Count == 0 && capsules.Count == 0)
        {
            if (useUndo) Undo.CollapseUndoOperations(undoGroup);
            return $"[S4 collider optimizer] {root.name}: no hull passed the box-fit threshold.";
        }

        var generated = new GameObject(GeneratedRootName);
        if (useUndo) Undo.RegisterCreatedObjectUndo(generated, "Create optimized collider root");
        generated.transform.SetParent(root.transform, false);

        var disabled = new HashSet<MeshCollider>();
        for (int i = 0; i < boxes.Count; i++)
        {
            BoxFit fit = boxes[i];
            var child = new GameObject($"Box_{i:000}");
            if (useUndo) Undo.RegisterCreatedObjectUndo(child, "Create optimized box collider");
            child.transform.SetParent(generated.transform, false);
            child.transform.localPosition = fit.center;
            child.transform.localRotation = fit.rotation;
            child.transform.localScale = Vector3.one;
            BoxCollider box = useUndo ? Undo.AddComponent<BoxCollider>(child) : child.AddComponent<BoxCollider>();
            box.center = Vector3.zero;
            box.size = fit.size;
            foreach (MeshCollider source in fit.sources) disabled.Add(source);
        }
        for (int i = 0; i < capsules.Count; i++)
        {
            CapsuleFit fit = capsules[i];
            var child = new GameObject($"Capsule_{i:000}");
            if (useUndo) Undo.RegisterCreatedObjectUndo(child, "Create optimized capsule collider");
            child.transform.SetParent(generated.transform, false);
            child.transform.localPosition = fit.center;
            child.transform.localRotation = fit.rotation;
            child.transform.localScale = Vector3.one;
            CapsuleCollider capsule = useUndo
                ? Undo.AddComponent<CapsuleCollider>(child)
                : child.AddComponent<CapsuleCollider>();
            capsule.center = Vector3.zero;
            capsule.direction = 1; // local Y
            capsule.radius = fit.radius;
            capsule.height = fit.height;
            foreach (MeshCollider source in fit.sources) disabled.Add(source);
        }
        foreach (MeshCollider source in disabled)
        {
            if (useUndo) Undo.RecordObject(source, "Disable replaced mesh collider");
            source.enabled = false;
            EditorUtility.SetDirty(source);
            MeshCollider[] siblings = source.GetComponents<MeshCollider>();
            int componentIndex = Array.IndexOf(siblings, source);
            var marker = new GameObject(DisabledMarkerPrefix + componentIndex);
            if (useUndo) Undo.RegisterCreatedObjectUndo(marker, "Mark replaced mesh collider");
            marker.transform.SetParent(source.transform, false);
            marker.tag = "EditorOnly";
        }

        EditorUtility.SetDirty(root);
        if (useUndo)
        {
            Undo.CollapseUndoOperations(undoGroup);
            Selection.activeGameObject = generated;
        }
        return $"[S4 collider optimizer] {root.name}: replaced {disabled.Count} convex " +
               $"MeshColliders by {boxes.Count} BoxColliders and {capsules.Count} CapsuleColliders " +
               $"({beforeMerge - boxes.Count} additional boxes removed by merging). " +
               $"{originals.Count - disabled.Count} complex MeshColliders were kept.";
    }

    void RestoreSelected()
    {
        GameObject selected = SelectedRoot();
        if (selected == null) return;
        if (IsPrefabAsset(selected, out string path))
        {
            GameObject contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                RestoreInternal(contents, false);
                PrefabUtility.SaveAsPrefabAsset(contents, path);
                AssetDatabase.SaveAssets();
                Debug.Log($"[S4 collider optimizer] Restored prefab asset: {path}");
            }
            finally { PrefabUtility.UnloadPrefabContents(contents); }
        }
        else
        {
            RestoreInternal(selected, true);
            Debug.Log($"[S4 collider optimizer] Restored collider setup on {selected.name}.", selected);
        }
        ShowNotification(new GUIContent("Original colliders restored"));
    }

    static void RestoreInternal(GameObject root, bool useUndo)
    {
        Transform generated = root.transform.Find(GeneratedRootName);
        if (generated != null)
        {
            if (useUndo) Undo.DestroyObjectImmediate(generated.gameObject);
            else DestroyImmediate(generated.gameObject);
        }
        // Restore only colliders disabled by this tool. Unrelated intentionally
        // disabled MeshColliders must stay disabled.
        var markers = root.GetComponentsInChildren<Transform>(true)
            .Where(t => t != root.transform && t.name.StartsWith(DisabledMarkerPrefix, StringComparison.Ordinal))
            .ToArray();
        foreach (Transform marker in markers)
        {
            if (int.TryParse(marker.name.Substring(DisabledMarkerPrefix.Length), out int index))
            {
                MeshCollider[] siblings = marker.parent.GetComponents<MeshCollider>();
                if (index >= 0 && index < siblings.Length)
                {
                    MeshCollider mc = siblings[index];
                    if (useUndo) Undo.RecordObject(mc, "Restore mesh collider");
                    mc.enabled = true;
                    EditorUtility.SetDirty(mc);
                }
            }
            if (useUndo) Undo.DestroyObjectImmediate(marker.gameObject);
            else DestroyImmediate(marker.gameObject);
        }
    }

    [Serializable]
    sealed class BoxFit
    {
        public Vector3 center;
        public Quaternion rotation;
        public Vector3 size;
        public float volume;
        public float fill;
        public readonly HashSet<MeshCollider> sources = new HashSet<MeshCollider>();
    }

    [Serializable]
    sealed class CapsuleFit
    {
        public Vector3 center;
        public Quaternion rotation;
        public float radius;
        public float height;
        public float volume;
        public float fill;
        public float elongation;
        public float radialAspect;
        public readonly HashSet<MeshCollider> sources = new HashSet<MeshCollider>();
    }

    bool IsCapsuleCandidate(CapsuleFit fit) =>
        fit != null && fit.fill >= minCapsuleFill &&
        fit.elongation >= minCapsuleElongation &&
        fit.radialAspect <= maxCapsuleRadialAspect;

    static bool TryFitCapsule(Transform root, MeshCollider mc, out CapsuleFit fit)
    {
        try { return TryFitCapsuleReadable(root, mc, out fit); }
        catch (Exception)
        {
            fit = null;
            return false;
        }
    }

    static bool TryFitCapsuleReadable(Transform root, MeshCollider mc, out CapsuleFit fit)
    {
        fit = null;
        Mesh mesh = mc.sharedMesh;
        if (mesh == null || mesh.vertexCount < 4) return false;
        Vector3[] vertices = mesh.vertices;
        var points = new Vector3[vertices.Length];
        Vector3 mean = Vector3.zero;
        for (int i = 0; i < vertices.Length; i++)
        {
            points[i] = root.InverseTransformPoint(mc.transform.TransformPoint(vertices[i]));
            mean += points[i];
        }
        mean /= points.Length;
        float meshVolume = ConvexMeshVolume(root, mc, vertices, mesh.triangles);
        if (meshVolume <= 1e-9f) return false;

        Quaternion basis = PrincipalRotation(points);
        Quaternion inv = Quaternion.Inverse(basis);
        var axial = new float[points.Length];
        var radialSq = new float[points.Length];
        float minT = float.PositiveInfinity, maxT = float.NegativeInfinity;
        float minY = float.PositiveInfinity, maxY = float.NegativeInfinity;
        float minZ = float.PositiveInfinity, maxZ = float.NegativeInfinity;
        for (int i = 0; i < points.Length; i++)
        {
            Vector3 q = inv * (points[i] - mean);
            axial[i] = q.x;
            radialSq[i] = q.y*q.y + q.z*q.z;
            minT = Mathf.Min(minT, q.x); maxT = Mathf.Max(maxT, q.x);
            minY = Mathf.Min(minY, q.y); maxY = Mathf.Max(maxY, q.y);
            minZ = Mathf.Min(minZ, q.z); maxZ = Mathf.Max(maxZ, q.z);
        }
        float span = maxT - minT;
        float crossY = maxY - minY, crossZ = maxZ - minZ;
        float crossMax = Mathf.Max(crossY, crossZ);
        float crossMin = Mathf.Max(1e-6f, Mathf.Min(crossY, crossZ));
        if (span <= 1e-6f || crossMax <= 1e-6f) return false;

        // Search capsule segment endpoints. Radius is the maximum distance to
        // that segment, so the resulting capsule contains the source hull.
        float bestVolume = float.PositiveInfinity, bestRadius = 0f;
        float bestA = minT, bestB = maxT;
        const int steps = 9;
        for (int ia = 0; ia < steps; ia++)
        for (int ib = 0; ib < steps; ib++)
        {
            float a = minT + span * 0.45f * ia / (steps - 1);
            float b = maxT - span * 0.45f * ib / (steps - 1);
            if (b < a) continue;
            float radiusSq = 0f;
            for (int i = 0; i < axial.Length; i++)
            {
                float outside = axial[i] < a ? a - axial[i] :
                                (axial[i] > b ? axial[i] - b : 0f);
                radiusSq = Mathf.Max(radiusSq, radialSq[i] + outside*outside);
            }
            float radius = Mathf.Sqrt(radiusSq);
            float cylinder = b - a;
            float volume = Mathf.PI*radius*radius*cylinder +
                           (4f/3f)*Mathf.PI*radius*radius*radius;
            if (volume < bestVolume)
            {
                bestVolume = volume; bestRadius = radius; bestA = a; bestB = b;
            }
        }
        if (bestRadius <= 1e-6f || float.IsNaN(bestVolume) || float.IsInfinity(bestVolume)) return false;
        Vector3 axis = (basis * Vector3.right).normalized;
        float midpoint = (bestA + bestB) * 0.5f;
        fit = new CapsuleFit {
            center = mean + axis * midpoint,
            rotation = Quaternion.FromToRotation(Vector3.up, axis),
            radius = bestRadius,
            height = (bestB - bestA) + 2f*bestRadius,
            volume = bestVolume,
            fill = Mathf.Clamp01(meshVolume / bestVolume),
            elongation = span / crossMax,
            radialAspect = crossMax / crossMin,
        };
        fit.sources.Add(mc);
        return IsFinite(fit.center) && fit.height >= 2f*fit.radius;
    }

    static bool TryFitBox(Transform root, MeshCollider mc, out BoxFit fit)
    {
        try { return TryFitBoxReadable(root, mc, out fit); }
        catch (Exception ex)
        {
            fit = null;
            Debug.LogWarning($"[S4 collider optimizer] Cannot read collider mesh '{mc.name}': {ex.Message}. " +
                             "Enable Read/Write on its Model Import Settings.", mc);
            return false;
        }
    }

    static bool TryFitBoxReadable(Transform root, MeshCollider mc, out BoxFit fit)
    {
        fit = null;
        Mesh mesh = mc.sharedMesh;
        if (mesh == null || mesh.vertexCount < 4) return false;
        Vector3[] meshVertices = mesh.vertices;
        var points = new Vector3[meshVertices.Length];
        for (int i = 0; i < points.Length; i++)
            points[i] = root.InverseTransformPoint(mc.transform.TransformPoint(meshVertices[i]));

        float meshVolume = ConvexMeshVolume(root, mc, meshVertices, mesh.triangles);
        if (meshVolume <= 1e-9f) return false;

        // Test root-aligned and PCA-aligned boxes and keep the tighter one.
        BoxFit axisAligned = FitAtRotation(points, Quaternion.identity);
        Quaternion pcaRotation = PrincipalRotation(points);
        BoxFit oriented = FitAtRotation(points, pcaRotation);
        fit = oriented.volume < axisAligned.volume ? oriented : axisAligned;
        fit.fill = Mathf.Clamp01(meshVolume / Mathf.Max(fit.volume, 1e-9f));
        fit.sources.Add(mc);
        return IsFinite(fit.center) && IsFinite(fit.size) && fit.size.x > 0f && fit.size.y > 0f && fit.size.z > 0f;
    }

    static float ConvexMeshVolume(Transform root, MeshCollider mc, Vector3[] vertices, int[] triangles)
    {
        var p = new Vector3[vertices.Length];
        Vector3 centroid = Vector3.zero;
        for (int i = 0; i < vertices.Length; i++)
        {
            p[i] = root.InverseTransformPoint(mc.transform.TransformPoint(vertices[i]));
            centroid += p[i];
        }
        centroid /= Mathf.Max(1, p.Length);
        double volume = 0.0;
        for (int i = 0; i + 2 < triangles.Length; i += 3)
        {
            Vector3 a = p[triangles[i]] - centroid;
            Vector3 b = p[triangles[i + 1]] - centroid;
            Vector3 c = p[triangles[i + 2]] - centroid;
            volume += Math.Abs(Vector3.Dot(a, Vector3.Cross(b, c))) / 6.0;
        }
        return (float)volume;
    }

    static BoxFit FitAtRotation(IReadOnlyList<Vector3> points, Quaternion rotation)
    {
        Quaternion inv = Quaternion.Inverse(rotation);
        Vector3 min = new Vector3(float.PositiveInfinity, float.PositiveInfinity, float.PositiveInfinity);
        Vector3 max = new Vector3(float.NegativeInfinity, float.NegativeInfinity, float.NegativeInfinity);
        for (int i = 0; i < points.Count; i++)
        {
            Vector3 q = inv * points[i];
            min = Vector3.Min(min, q);
            max = Vector3.Max(max, q);
        }
        Vector3 size = max - min;
        return new BoxFit {
            center = rotation * ((min + max) * 0.5f),
            rotation = rotation,
            size = size,
            volume = Mathf.Max(0f, size.x * size.y * size.z),
            fill = 0f,
        };
    }

    static Quaternion PrincipalRotation(IReadOnlyList<Vector3> points)
    {
        Vector3 mean = Vector3.zero;
        for (int i = 0; i < points.Count; i++) mean += points[i];
        mean /= Mathf.Max(1, points.Count);

        // Symmetric covariance matrix.
        float xx=0, xy=0, xz=0, yy=0, yz=0, zz=0;
        for (int i = 0; i < points.Count; i++)
        {
            Vector3 d = points[i] - mean;
            xx += d.x*d.x; xy += d.x*d.y; xz += d.x*d.z;
            yy += d.y*d.y; yz += d.y*d.z; zz += d.z*d.z;
        }
        Vector3 major = PowerEigen(xx,xy,xz,yy,yz,zz, new Vector3(0.73f,0.41f,0.55f), Vector3.zero);
        Vector3 second = PowerEigen(xx,xy,xz,yy,yz,zz, new Vector3(0.17f,0.91f,0.37f), major);
        Vector3 third = Vector3.Cross(major, second).normalized;
        if (third.sqrMagnitude < 0.5f) return Quaternion.identity;
        second = Vector3.Cross(third, major).normalized;
        // local X=major, local Y=second, local Z=third
        return Quaternion.LookRotation(third, second);
    }

    static Vector3 PowerEigen(float xx,float xy,float xz,float yy,float yz,float zz,
                              Vector3 seed, Vector3 reject)
    {
        Vector3 v = seed.normalized;
        for (int i = 0; i < 24; i++)
        {
            Vector3 w = new Vector3(xx*v.x + xy*v.y + xz*v.z,
                                    xy*v.x + yy*v.y + yz*v.z,
                                    xz*v.x + yz*v.y + zz*v.z);
            if (reject.sqrMagnitude > 0.5f) w -= reject * Vector3.Dot(w, reject);
            if (w.sqrMagnitude < 1e-12f) break;
            v = w.normalized;
        }
        return v;
    }

    List<BoxFit> MergeBoxes(List<BoxFit> input)
    {
        var boxes = new List<BoxFit>(input);
        bool changed = true;
        while (changed)
        {
            changed = false;
            float bestError = float.PositiveInfinity;
            int bestI = -1, bestJ = -1;
            BoxFit best = null;
            for (int i = 0; i < boxes.Count; i++)
            for (int j = i + 1; j < boxes.Count; j++)
            {
                if (!TryMergeBoxes(boxes[i], boxes[j], out BoxFit merged, out float error)) continue;
                if (error < bestError) { bestError = error; bestI = i; bestJ = j; best = merged; }
            }
            if (best != null)
            {
                boxes[bestI] = best;
                boxes.RemoveAt(bestJ);
                changed = true;
            }
        }
        return boxes;
    }

    bool TryMergeBoxes(BoxFit a, BoxFit b, out BoxFit merged, out float inflation)
    {
        merged = null;
        inflation = float.PositiveInfinity;
        float angle = Quaternion.Angle(a.rotation, b.rotation);
        // PCA can flip equivalent axes by 180 degrees.
        angle = Mathf.Min(angle, Mathf.Abs(180f - angle));
        if (angle > maxAxisAngle) return false;

        Quaternion inv = Quaternion.Inverse(a.rotation);
        Vector3 ac = inv * a.center;
        Vector3 bc = inv * b.center;
        Vector3 ah = a.size * 0.5f;
        // Project B's oriented extents into A's frame.
        Matrix4x4 rel = Matrix4x4.Rotate(inv * b.rotation);
        Vector3 bh = b.size * 0.5f;
        Vector3 projected = new Vector3(
            Mathf.Abs(rel.m00)*bh.x + Mathf.Abs(rel.m01)*bh.y + Mathf.Abs(rel.m02)*bh.z,
            Mathf.Abs(rel.m10)*bh.x + Mathf.Abs(rel.m11)*bh.y + Mathf.Abs(rel.m12)*bh.z,
            Mathf.Abs(rel.m20)*bh.x + Mathf.Abs(rel.m21)*bh.y + Mathf.Abs(rel.m22)*bh.z);
        Vector3 amin = ac-ah, amax = ac+ah, bmin = bc-projected, bmax = bc+projected;

        float dx = IntervalGap(amin.x,amax.x,bmin.x,bmax.x);
        float dy = IntervalGap(amin.y,amax.y,bmin.y,bmax.y);
        float dz = IntervalGap(amin.z,amax.z,bmin.z,bmax.z);
        if (Mathf.Sqrt(dx*dx + dy*dy + dz*dz) > contactEpsilon) return false;

        Vector3 min = Vector3.Min(amin,bmin), max = Vector3.Max(amax,bmax);
        Vector3 size = max-min;
        float mergedVolume = size.x*size.y*size.z;
        Vector3 overlap = Vector3.Max(Vector3.zero, Vector3.Min(amax,bmax)-Vector3.Max(amin,bmin));
        float intersection = overlap.x*overlap.y*overlap.z;
        float union = Mathf.Max(1e-9f, a.volume+b.volume-intersection);
        inflation = Mathf.Max(0f, (mergedVolume-union)/union);
        if (inflation > maxMergeInflation) return false;

        merged = new BoxFit {
            center = a.rotation*((min+max)*0.5f), rotation=a.rotation,
            size=size, volume=mergedVolume, fill=union/mergedVolume,
        };
        foreach (MeshCollider s in a.sources) merged.sources.Add(s);
        foreach (MeshCollider s in b.sources) merged.sources.Add(s);
        return true;
    }

    static float IntervalGap(float amin,float amax,float bmin,float bmax)
    {
        if (amax < bmin) return bmin-amax;
        if (bmax < amin) return amin-bmax;
        return 0f;
    }

    static bool IsFinite(Vector3 v) =>
        !(float.IsNaN(v.x)||float.IsNaN(v.y)||float.IsNaN(v.z)||
          float.IsInfinity(v.x)||float.IsInfinity(v.y)||float.IsInfinity(v.z));
}
#endif
