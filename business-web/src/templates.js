import { businessApi } from "./api.js";

const codePattern = /^[a-z][a-z0-9_]{0,63}$/;

export function validateTemplateDraft(draft) {
  if (!codePattern.test(draft.code)) throw new Error("模板代码须以小写字母开头，只能包含小写字母、数字和下划线（最多 64 字符）。");
  if (!draft.name.trim() || draft.name.trim().length > 128) throw new Error("模板名称须为 1–128 字符。");
  if (!draft.fields.length || draft.fields.length > 100) throw new Error("模板需要 1–100 个字段。");
  const seen = new Set();
  for (const field of draft.fields) {
    if (!codePattern.test(field.code) || seen.has(field.code)) throw new Error("字段代码须唯一，且只能使用小写字母、数字和下划线。");
    if (!field.label.trim() || field.label.trim().length > 128) throw new Error("字段名称须为 1–128 字符。");
    if (!["text", "date", "list"].includes(field.type)) throw new Error("字段类型只能是文本、日期或列表。");
    seen.add(field.code);
  }
  return {
    code: draft.code, name: draft.name.trim(),
    fields: draft.fields.map((field) => ({
      code: field.code, label: field.label.trim(), type: field.type, required: Boolean(field.required),
    })),
  };
}

function element(tag, className = "", content = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content) node.textContent = content;
  return node;
}

function button(label, handler) {
  const node = element("button", "secondary-button", label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function input(labelText, value, name, required = true) {
  const label = element("label", "template-field-label", labelText);
  const control = element("input");
  control.name = name;
  control.value = value;
  control.required = required;
  label.append(control);
  return label;
}

export function createTemplateManager(root, { onChanged }) {
  let templates = [];
  let selectedCode = null;
  let creating = false;
  let busy = false;
  let message = "";
  let error = "";
  let viewVersion = 0;

  async function refreshAfterWrite(updated) {
    try {
      await onChanged(updated);
      return "";
    } catch (cause) {
      return `模板列表刷新失败：${cause.message}；已按写入响应更新，可稍后刷新页面核对。`;
    }
  }

  function fieldRow(field = { code: "", label: "", type: "text", required: false }) {
    const row = element("div", "template-field-row");
    row.append(input("字段代码", field.code, "field-code"), input("显示名称", field.label, "field-label"));
    const typeLabel = element("label", "template-field-label", "字段类型");
    const type = element("select");
    type.name = "field-type";
    for (const [value, label] of [["text", "文本"], ["date", "日期"], ["list", "列表"]]) {
      const option = element("option", "", label);
      option.value = value;
      type.append(option);
    }
    type.value = field.type;
    typeLabel.append(type);
    const required = element("label", "template-required", "必填");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.name = "field-required";
    checkbox.checked = field.required;
    required.prepend(checkbox);
    row.append(typeLabel, required);
    row.append(button("上移", () => { if (row.previousElementSibling) row.previousElementSibling.before(row); }));
    row.append(button("下移", () => { if (row.nextElementSibling) row.nextElementSibling.after(row); }));
    row.append(button("移除", () => row.remove()));
    return row;
  }

  function render() {
    root.replaceChildren();
    const intro = element("p", "review-hint", "内置模板不可修改；自定义模板每次保存都生成新版本。旧文档继续使用上传时绑定的版本。此系统没有用户或审批权限。");
    const list = element("div", "template-list");
    for (const template of templates) {
      const choose = button(`${template.name} · v${template.version}${template.enabled ? "" : " · 已停用"}${template.built_in ? " · 内置" : ""}`, () => {
        const keepFocus = document.activeElement === choose;
        viewVersion += 1;
        selectedCode = template.code;
        creating = false;
        error = "";
        message = "";
        render();
        if (keepFocus) {
          [...root.querySelectorAll(".template-list button")].find((node) => node.dataset.templateCode === template.code)?.focus({ preventScroll: true });
        }
      });
      choose.dataset.templateCode = template.code;
      choose.classList.toggle("selected", selectedCode === template.code && !creating);
      list.append(choose);
    }
    list.append(button("新增自定义模板", () => {
      const keepFocus = root.contains(document.activeElement);
      viewVersion += 1;
      selectedCode = null;
      creating = true;
      error = "";
      message = "";
      render();
      if (keepFocus) root.querySelector('[name="template-code"]')?.focus({ preventScroll: true });
    }));
    root.append(intro, list);
    if (error) {
      const warning = element("p", "error-banner", error);
      warning.setAttribute("role", "alert");
      root.append(warning);
    }
    if (message) {
      const success = element("p", "template-success", message);
      success.setAttribute("role", "status");
      root.append(success);
    }
    const selected = templates.find((item) => item.code === selectedCode);
    if (!selected && !creating) return;
    if (selected && (selected.built_in || !selected.enabled)) {
      root.append(element("h3", "", `${selected.name} · 第 ${selected.version} 版`));
      root.append(element("p", "review-hint", selected.built_in ? "内置模板只读；需要不同字段时，请新建自定义模板。" : "此模板已停用；历史版本与已绑定文档仍可读取。"));
      for (const field of selected.fields) {
        root.append(element("p", "template-readonly-field", `${field.label} (${field.code}) · ${field.type}${field.required ? " · 必填" : ""}`));
      }
      return;
    }

    const form = element("form", "template-editor");
    form.append(element("h3", "", creating ? "新建自定义模板" : `编辑 ${selected.name} · 保存为第 ${selected.version + 1} 版`));
    const code = input("模板代码（创建后不可更改）", selected?.code || "", "template-code");
    code.querySelector("input").disabled = !creating;
    const name = input("模板名称", selected?.name || "", "template-name");
    form.append(code, name);
    const fields = element("div", "template-fields");
    for (const field of selected?.fields || [{ code: "", label: "", type: "text", required: false }]) {
      fields.append(fieldRow(field));
    }
    form.append(element("h4", "", "字段配置 · 从上到下为提取顺序"), fields);
    form.append(button("添加字段", () => { if (fields.children.length < 100) fields.append(fieldRow()); }));
    const save = element("button", "primary-button", creating ? "创建模板" : "保存新版本");
    save.type = "submit";
    form.append(save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy) return;
      const savedViewVersion = viewVersion;
      try {
        const draft = validateTemplateDraft({
          code: selected?.code || code.querySelector("input").value.trim(),
          name: name.querySelector("input").value,
          fields: [...fields.children].map((row) => ({
            code: row.querySelector('[name="field-code"]').value.trim(),
            label: row.querySelector('[name="field-label"]').value,
            type: row.querySelector('[name="field-type"]').value,
            required: row.querySelector('[name="field-required"]').checked,
          })),
        });
        busy = true;
        save.disabled = true;
        const updated = creating
          ? await businessApi.createTemplate(draft)
          : await businessApi.updateTemplate(selected.code, { name: draft.name, fields: draft.fields });
        if (savedViewVersion === viewVersion) {
          selectedCode = updated.code;
          creating = false;
        }
        const refreshWarning = await refreshAfterWrite(updated);
        if (savedViewVersion === viewVersion) {
          message = `${updated.name} 第 ${updated.version} 版已保存。${refreshWarning}`;
          error = "";
        }
      } catch (cause) {
        if (savedViewVersion === viewVersion) error = cause.message;
      } finally {
        busy = false;
        render();
      }
    });
    root.append(form);
    if (selected) {
      root.append(button("停用此自定义模板", async () => {
        if (busy || !window.confirm(`停用“${selected.name}”？新上传将无法选择，历史文档与版本不会删除。`)) return;
        busy = true;
        const savedViewVersion = viewVersion;
        try {
          const updated = await businessApi.disableTemplate(selected.code);
          const refreshWarning = await refreshAfterWrite(updated);
          if (savedViewVersion === viewVersion) {
            message = `${selected.name} 已停用；历史版本仍可读取。${refreshWarning}`;
            error = "";
          }
        } catch (cause) {
          if (savedViewVersion === viewVersion) error = cause.message;
        } finally {
          busy = false;
          render();
        }
      }));
    }
  }

  return {
    setTemplates(items) {
      templates = items;
      if (selectedCode && !templates.some((item) => item.code === selectedCode)) selectedCode = null;
      render();
    },
  };
}
