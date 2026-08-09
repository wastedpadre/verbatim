import { useCallback, useEffect, useState } from "react";
import Tip from "./Tip";
import { getSettings, listPolishModels, saveSettings, testPolish } from "../api";

/** Keys and model IDs that only matter when their provider is selected. */
const PROVIDER_FIELDS = {
  gemini: ["GEMINI_API_KEY", "POLISH_MODEL", "POLISH_THINKING"],
  openai: ["OPENAI_API_KEY", "OPENAI_MODEL"],
  anthropic: ["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"],
};
const ALL_PROVIDER_FIELDS = Object.values(PROVIDER_FIELDS).flat();

/** Which settings field holds the model ID for the selected provider. */
const MODEL_FIELD = {
  gemini: "POLISH_MODEL",
  openai: "OPENAI_MODEL",
  anthropic: "ANTHROPIC_MODEL",
};

function Field({ field, value, onChange }) {
  const { name, type, label, hint, secret, choices } = field;

  if (type === "bool") {
    return (
      <label className="set-row toggle-row">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(name, e.target.checked)}
        />
        <span className="set-label">
          {label}
          <span className="set-hint">{hint}</span>
        </span>
      </label>
    );
  }

  if (choices) {
    return (
      <label className="set-row">
        <span className="set-label">
          {label}
          <span className="set-hint">{hint}</span>
        </span>
        <select
          className="field"
          value={value ?? ""}
          onChange={(e) => onChange(name, e.target.value)}
        >
          {choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
    );
  }

  return (
    <label className="set-row">
      <span className="set-label">
        {label}
        <span className="set-hint">{hint}</span>
      </span>
      <input
        className="field"
        type={secret ? "password" : type === "str" ? "text" : "number"}
        step={type === "float" ? "0.01" : undefined}
        value={value ?? ""}
        placeholder={secret ? "not set" : ""}
        onChange={(e) => onChange(name, e.target.value)}
      />
    </label>
  );
}

/**
 * The webhook URL, built from the address the browser is already using.
 *
 * Hardcoding a host here would be wrong for exactly the people who need it:
 * Sonarr usually runs on the same box under a different container name, so
 * the URL that works is the one the user reached this page on.
 */
function SonarrHook({ token }) {
  const [copied, setCopied] = useState(false);
  const url =
    `${window.location.origin}/api/webhook/sonarr` +
    (token && !token.startsWith("•") ? `?token=${encodeURIComponent(token)}` : "");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="hook">
      <span className="set-hint">
        In Sonarr: <strong>Settings → Connect → + → Webhook</strong>, method POST,
        triggers <strong>On Import</strong> and <strong>On Upgrade</strong>. To
        caption only some of your library, tag those series in Sonarr and set the
        same tag on the connection — Sonarr then never calls out for anything
        else, which beats filtering here.
      </span>
      <div className="hook-url">
        <code>{url}</code>
        <Tip text="Copy the URL, then paste it into Sonarr's webhook URL field.">
          <button className="btn ghost" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </Tip>
      </div>
      <span className="set-hint">
        Sonarr's <em>Test</em> button should return success immediately — it never
        queues anything. If real imports do nothing, the paths don't match: check
        the container log for “is not a file here” and set a translation above.
      </span>
    </div>
  );
}

/**
 * Settings that take effect on the next job, without recreating the container.
 *
 * Docker only reads --env-file when a container is created, so every toggle
 * previously meant editing .env, removing the container and running it again.
 * These are re-read per job, so they're safe to change live and are persisted
 * to /config so they survive a recreate.
 */
export default function Settings({ onClose }) {
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [test, setTest] = useState(null);
  const [models, setModels] = useState(null);
  const [touched, setTouched] = useState(() => new Set());

  useEffect(() => {
    getSettings()
      .then((d) => {
        setSchema(d.schema);
        setValues(d.values);
      })
      .catch((e) => setNotice({ bad: true, text: e.message }));
  }, []);

  const provider = values.POLISH_PROVIDER || "gemini";

  const change = useCallback((name, v) => {
    setValues((prev) => ({ ...prev, [name]: v }));
    setDirty(true);
    setTouched((prev) => new Set(prev).add(name));
    // A model list or a connection result belongs to the provider it came
    // from; leaving either on screen after a switch reads as a live status
    // for the new one.
    if (name === "POLISH_PROVIDER") {
      setModels(null);
      setTest(null);
    }
  }, []);

  /** Names of restart-class fields the user has edited but not yet saved. */
  const pendingRestart = (schema || [])
    .flatMap((g) => g.fields)
    .filter((f) => f.restart && touched.has(f.name))
    .map((f) => f.label);

  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await saveSettings(values);
      setValues(res.values);
      setDirty(false);
      // Saying "applies to the next job" after a model change would be
      // wrong, and wrong in the direction that wastes a whole episode
      // before anyone notices.
      setNotice(
        pendingRestart.length
          ? {
              text: `Saved. ${pendingRestart.join(" and ")} ` +
                `${pendingRestart.length > 1 ? "take" : "takes"} effect when the ` +
                `container next starts — everything else applies to the next job.`,
            }
          : { text: "Saved. Applies to the next job." }
      );
      setTouched(new Set());
    } catch (e) {
      setNotice({ bad: true, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setBusy(true);
    setTest(null);
    try {
      if (dirty) await saveSettings(values);
      setTest(await testPolish());
      setDirty(false);
    } catch (e) {
      setTest({ ok: false, detail: e.message });
    } finally {
      setBusy(false);
    }
  };

  const loadModels = async () => {
    setBusy(true);
    setNotice(null);
    try {
      // Both of these ask the *server* which provider is selected, so an
      // unsaved switch would list the previous provider's models under the
      // new provider's name.
      if (dirty) {
        await saveSettings(values);
        setDirty(false);
      }
      const d = await listPolishModels();
      setModels(d.models);
    } catch (e) {
      setNotice({ bad: true, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings" role="dialog" aria-modal="true" aria-label="Settings">
      <header className="editor-head">
        <button className="btn ghost" onClick={onClose}>← Back</button>
        <div className="editor-title">
          <h2>Settings</h2>
          <span>Applies to the next job. No restart needed.</span>
        </div>
        <div className="editor-actions">
          {notice && (
            <span className={notice.bad ? "editor-error inline" : "notice"}>
              {notice.text}
            </span>
          )}
          {dirty && <span className="dirty">unsaved</span>}
          <Tip text="Writes to /config/settings.json and applies to the next job. Survives a container recreate; nothing restarts.">
            <button className="btn" onClick={save} disabled={busy || !dirty}>
              {busy ? "Saving…" : "Save"}
            </button>
          </Tip>
        </div>
      </header>

      <div className="settings-body">
        {!schema && <div className="empty">Loading settings…</div>}

        {schema?.map((group) => (
          <section className="set-group" key={group.key}>
            <h3>{group.title}</h3>
            <p className="set-blurb">{group.blurb}</p>

            {group.fields
              // Showing three keys and three model IDs at once invites
              // filling in the wrong pair. Only the selected provider's
              // fields are rendered.
              .filter(
                (f) =>
                  !ALL_PROVIDER_FIELDS.includes(f.name) ||
                  (PROVIDER_FIELDS[provider] || []).includes(f.name)
              )
              .map((f) => (
                <Field key={f.name} field={f} value={values[f.name]} onChange={change} />
              ))}

            {group.key === "model" && (
              <p className="set-warn">
                <strong>Takes effect on the next container start</strong>, not the
                next job — the model is loaded once at startup and held in VRAM.
                Restart the container from the Docker tab, or{" "}
                <code>docker compose restart</code>. Switching to a model you
                haven't used before downloads it first (about 3 GB for large-v3),
                so that first job is slow; it's cached in <code>/config</code>{" "}
                after that.
              </p>
            )}

            {group.key === "sonarr" && <SonarrHook token={values.SONARR_TOKEN} />}

            {group.key === "polish" && (
              <div className="set-tools">
                <Tip text="Sends one throwaway prompt to the provider right now, so a bad key or a retired model ID shows up here instead of failing mid-job.">
                  <button className="btn ghost" onClick={runTest} disabled={busy}>
                    Test connection
                  </button>
                </Tip>
                <Tip text="Asks the provider which models this key can actually call. Beats guessing an ID that may have been retired.">
                  <button className="btn ghost" onClick={loadModels} disabled={busy}>
                    List available models
                  </button>
                </Tip>

                {test && (
                  <p className={test.ok ? "test-ok" : "test-bad"}>
                    {test.ok ? "Working — " : "Failed — "}
                    {test.detail}
                  </p>
                )}

                {models && (
                  <div className="model-list">
                    <span className="set-hint">
                      Models your {provider} key can call. Click one to use it.
                    </span>
                    <div className="terms">
                      {models.map((m) => (
                        <button
                          key={m}
                          className="term"
                          onClick={() => change(MODEL_FIELD[provider], m)}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>
        ))}

        <p className="set-footnote">
          Everything here is written to <code>/config/settings.json</code> and
          layered over your <code>.env</code> at startup, so it survives a
          container recreate — and so a value set here wins over the same
          variable in <code>.env</code> from then on. Paths and{" "}
          <code>DEVICE</code> stay env-only: the mounts can't change without
          recreating the container anyway, and the only reason to leave{" "}
          <code>cuda</code> is a CPU test that runs about ten times slower.
        </p>
      </div>
    </div>
  );
}
