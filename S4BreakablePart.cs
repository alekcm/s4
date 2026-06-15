using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Generic per-part breakable component for Sims 4 extracted prefabs.
/// 
/// Expected hierarchy:
/// PartRoot (this component)
///   ├─ Intact        <- intactRoot
///   ├─ Broken_A      <- brokenRoots[0]
///   └─ Broken_B      <- brokenRoots[1]
/// 
/// You can also have more than 2 broken pieces.
/// </summary>
public class S4BreakablePart : MonoBehaviour
{
    [Header("Scene references")]
    public GameObject intactRoot;
    public GameObject[] brokenRoots;

    [Header("Behaviour")]
    public bool breakOnCollision = false;
    public float breakImpulseThreshold = 8f;
    public bool detachBrokenPieces = false;
    public bool applyExplosionImpulse = true;
    public float explosionForce = 2f;
    public float explosionRadius = 0.25f;
    public float upwardModifier = 0.05f;

    [Header("State")]
    [SerializeField] private bool isBroken = false;

    public bool IsBroken => isBroken;

    void Awake()
    {
        if (intactRoot == null || brokenRoots == null || brokenRoots.Length == 0)
        {
            AutoAssignFromChildren();
        }

        ApplyCurrentStateImmediate();
    }

    public void Break()
    {
        Break(Vector3.zero, transform.position, false);
    }

    public void Break(Vector3 impulse)
    {
        Break(impulse, transform.position, true);
    }

    public void Break(Vector3 impulse, Vector3 point)
    {
        Break(impulse, point, true);
    }

    public void Restore()
    {
        isBroken = false;

        if (intactRoot != null)
        {
            intactRoot.SetActive(true);
            SetRigidbodiesKinematic(intactRoot, false);
        }

        if (brokenRoots != null)
        {
            foreach (var root in brokenRoots)
            {
                if (root == null) continue;
                root.SetActive(false);
                ResetRigidbodies(root);
            }
        }
    }

    [ContextMenu("Auto Assign From Children")]
    public void AutoAssignFromChildren()
    {
        var broken = new List<GameObject>();

        for (int i = 0; i < transform.childCount; i++)
        {
            var child = transform.GetChild(i).gameObject;
            var lower = child.name.ToLowerInvariant();

            if (intactRoot == null && lower.Contains("intact"))
            {
                intactRoot = child;
                continue;
            }

            if (lower.Contains("broken") || lower.Contains("fracture") || lower.Contains("shard"))
            {
                broken.Add(child);
            }
        }

        // Fallback naming if exporter used Broken_A / Broken_B groups not exact naming.
        if (intactRoot == null && transform.childCount > 0)
        {
            for (int i = 0; i < transform.childCount; i++)
            {
                var child = transform.GetChild(i).gameObject;
                if (!child.name.ToLowerInvariant().Contains("broken"))
                {
                    intactRoot = child;
                    break;
                }
            }
        }

        brokenRoots = broken.ToArray();
    }

    [ContextMenu("Break")]
    public void DebugBreak()
    {
        Break();
    }

    [ContextMenu("Restore")]
    public void DebugRestore()
    {
        Restore();
    }

    void OnCollisionEnter(Collision collision)
    {
        if (!breakOnCollision || isBroken || collision == null) return;
        if (collision.relativeVelocity.magnitude >= breakImpulseThreshold)
        {
            Break(collision.relativeVelocity, collision.GetContact(0).point);
        }
    }

    private void Break(Vector3 impulse, Vector3 point, bool applyImpulse)
    {
        if (isBroken) return;
        isBroken = true;

        if (intactRoot != null)
        {
            SetRigidbodiesKinematic(intactRoot, true);
            intactRoot.SetActive(false);
        }

        if (brokenRoots == null) return;

        foreach (var root in brokenRoots)
        {
            if (root == null) continue;

            if (detachBrokenPieces)
            {
                root.transform.SetParent(null, true);
            }

            root.SetActive(true);
            WakeRigidbodies(root);

            if (applyImpulse)
            {
                ApplyImpulse(root, impulse, point);
            }
        }
    }

    private void ApplyCurrentStateImmediate()
    {
        if (!isBroken)
        {
            if (intactRoot != null) intactRoot.SetActive(true);
            if (brokenRoots != null)
            {
                foreach (var root in brokenRoots)
                {
                    if (root == null) continue;
                    root.SetActive(false);
                }
            }
            return;
        }

        if (intactRoot != null) intactRoot.SetActive(false);
        if (brokenRoots != null)
        {
            foreach (var root in brokenRoots)
            {
                if (root == null) continue;
                root.SetActive(true);
            }
        }
    }

    private static void SetRigidbodiesKinematic(GameObject root, bool kinematic)
    {
        foreach (var rb in root.GetComponentsInChildren<Rigidbody>(true))
        {
            rb.isKinematic = kinematic;
        }
    }

    private static void WakeRigidbodies(GameObject root)
    {
        foreach (var rb in root.GetComponentsInChildren<Rigidbody>(true))
        {
            rb.isKinematic = false;
            rb.WakeUp();
        }
    }

    private void ApplyImpulse(GameObject root, Vector3 impulse, Vector3 point)
    {
        foreach (var rb in root.GetComponentsInChildren<Rigidbody>(true))
        {
            if (applyExplosionImpulse)
            {
                rb.AddExplosionForce(explosionForce + impulse.magnitude * 0.25f, point, explosionRadius, upwardModifier, ForceMode.Impulse);
            }
            else
            {
                rb.AddForce(impulse * 0.25f, ForceMode.Impulse);
            }
        }
    }

    private static void ResetRigidbodies(GameObject root)
    {
        foreach (var rb in root.GetComponentsInChildren<Rigidbody>(true))
        {
            rb.velocity = Vector3.zero;
            rb.angularVelocity = Vector3.zero;
            rb.Sleep();
        }
    }
}
