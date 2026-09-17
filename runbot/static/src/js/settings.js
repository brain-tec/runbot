import { CookieStorage } from "./utils/storage";

/**
 * User preferences of the runbot frontend backed by cookies
 */
class SettingsManager extends CookieStorage {}

export const settings = new SettingsManager();

/**
 * Applies the setting declared by `data-setting`/`data-setting-value`.
 * Immediately applied with a reload.
 */
document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-setting]");
    if (!el || !el.checked) {
        return;
    }
    settings.set(el.dataset.setting, el.dataset.settingValue);
    window.location.reload();
});
