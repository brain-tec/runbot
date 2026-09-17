import { CookieStorage } from "./utils/storage";

/**
 * User preferences of the runbot frontend backed by cookies
 */
class SettingsManager extends CookieStorage {}

export const settings = new SettingsManager();

/**
 * Applies the setting declared by `data-setting` on a toggled input.
 * A `data-setting-value` gives the value to store when the input is checked,
 * otherwise the input is a boolean and stores `1` or `0`.
 * Immediately applied with a reload.
 */
document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-setting]");
    if (!el) {
        return;
    }
    const { setting, settingValue } = el.dataset;
    if (settingValue === undefined) {
        settings.set(setting, el.checked ? "1" : "0");
    } else if (el.checked) {
        settings.set(setting, settingValue);
    } else {
        return;
    }
    window.location.reload();
});
