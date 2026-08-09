import { useCallback, useEffect, useState } from "react";
import { getSettings, listPolishModels, saveSettings, testPolish } from "../api";

function Field({ field, value, onChange }) {
  const { name, type, label, hint, secret } = field;

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

  useEffect(() => {
    getSettings()
      .then((d) => {
        setSchema(d.schema);
        setValues(d.values);
      })
      .catch((e) => setNotice({ bad: true, text: e.message }));
  }, []);

  const change = useCallback((name, v) => {
    setValues((prev) => ({ ...prev, [name]: v }));
    setDirty(true);
  }, []);

  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await saveSettings(values);
      setValues(res.values);
      setDirty(false);
      setNotice({ text: "Saved. Applies to the next job." });
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
    try {
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
          <button className="btn" onClick={save} disabled={busy || !dirty}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      <div className="settings-body">
        {!schema && <div className="empty">Loading settings…</div>}

        {schema?.map((group) => (
          <section className="set-group" key={group.key}>
            <h3>{group.title}</h3>
            <p className="set-blurb">{group.blurb}</p>

            {group.fields.map((f) => (
              <Field key={f.name} field={f} value={values[f.name]} onChange={change} />
            ))}

            {group.key === "polish" && (
              <div className="set-tools">
                <button className="btn ghost" onClick={runTest} disabled={busy}>
                  Test connection
                </button>
                <button className="btn ghost" onClick={loadModels} disabled={busy}>
                  List available models
                </button>

                {test && (
                  <p className={test.ok ? "test-ok" : "test-bad"}>
                    {test.ok ? "Working — " : "Failed — "}
                    {test.detail}
                  </p>
                )}

                {models && (
                  <div className="model-list">
                    <span className="set-hint">
                      Models your key can call. Click one to use it.
                    </span>
                    <div className="terms">
                      {models.map((m) => (
                        <button
                          key={m}
                          className="term"
                          onClick={() => change("POLISH_MODEL", m)}
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
          Model size, device and paths are read once when the container starts,
          so they stay in your .env rather than appearing here where changing
          them would look like it worked without actually taking effect.
        </p>
      </div>
    </div>
  );
}
