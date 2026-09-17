import { CookieStorage } from "./utils/storage";

/**
 * User preferences of the runbot frontend backed by cookies
 */
class SettingsManager extends CookieStorage {}

export const settings = new SettingsManager();
