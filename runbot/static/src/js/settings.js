import { CookieStorage } from "./utils/storage";

/**
 * User preferences of the runbot frontend backed by cookies
 */
class SettingsManager extends CookieStorage {}

export const settings = new SettingsManager();

/**
 * Applies the setting named by `data-setting` on a toggled input: a checkbox
 * stores whether it is checked, any other input stores its value once checked.
 * Immediately applied with a reload.
 */
document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-setting]");
    if (!el) {
        return;
    }
    if (el.type === "checkbox") {
        settings.set(el.dataset.setting, el.checked ? "1" : "0");
    } else if (el.checked) {
        settings.set(el.dataset.setting, el.value);
    } else {
        return;
    }
    window.location.reload();
});
