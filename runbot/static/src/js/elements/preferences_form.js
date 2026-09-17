import { settings } from "../utils/settings";

/**
 * A form handling the user preferences: every `[data-setting]` it contains is
 * stored when it is submitted, and a control tagged `data-toggle="submit"`
 * submits it as soon as it is toggled instead of waiting for the submit button.
 *
 * A setting is reset instead of being stored when its value matches the
 * `data-setting-default` rendered by the server, so that the default keeps
 * applying when it changes.
 */
class PreferencesForm extends HTMLElement {
    static settingSelector = "[data-setting]";
    static groupSelector = "fieldset[data-setting]";
    static autoSubmitSelector = "[data-toggle='submit']";
    static presetSelector = "[data-toggle='preset']";

    connectedCallback() {
        this.addEventListener("change", this.onChange);
        this.addEventListener("submit", this.onSubmit);
        this.addEventListener("click", this.onSelection);
    }

    disconnectedCallback() {
        this.removeEventListener("change", this.onChange);
        this.removeEventListener("submit", this.onSubmit);
        this.removeEventListener("click", this.onSelection);
    }

    get groups() {
        return [...this.querySelectorAll(PreferencesForm.groupSelector)];
    }

    onChange(event) {
        if (event.target.matches(PreferencesForm.autoSubmitSelector)) {
            event.target.form.requestSubmit();
        }
    }

    onSubmit(event) {
        event.preventDefault();
        const data = new FormData(event.target);
        for (const el of this.querySelectorAll(PreferencesForm.settingSelector)) {
            const { setting, settingDefault } = el.dataset;
            const value = this.settingValue(el, data);
            if (value === undefined) {
                continue;
            }
            if (value === settingDefault) {
                settings.remove(setting);
            } else {
                settings.set(setting, value);
            }
        }
        window.location.reload();
    }

    /**
     * @returns {string|undefined} the value held by a setting element: a group
     *  joins the values of its checked boxes, a checkbox tells whether it is
     *  checked and any other input holds its own value, unless it is not the
     *  checked one of a group of choices.
     */
    settingValue(el, data) {
        if (el.matches(PreferencesForm.groupSelector)) {
            return data.getAll(el.dataset.setting).sort().join("-");
        }
        if (el.type === "checkbox") {
            return el.checked ? "1" : "0";
        }
        return el.checked ? el.value : undefined;
    }

    /**
     * Checks the boxes of every group, none of them, or the ones the setting
     * defaults to, depending on the clicked button.
     */
    onSelection(event) {
        const button = event.target.closest(PreferencesForm.presetSelector);
        if (!button) {
            return;
        }
        for (const group of this.groups) {
            const defaults = group.dataset.settingDefault?.split("-") ?? [];
            for (const input of group.querySelectorAll("input")) {
                input.checked =
                    button.value === "all" ||
                    (button.value === "default" && defaults.includes(input.value));
            }
        }
    }
}

customElements.define("preferences-form", PreferencesForm);
