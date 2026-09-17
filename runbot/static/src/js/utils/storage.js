/**
 * Interface of a simple key/value store of strings.
 *
 * @abstract
 */
export class StorageAdapter {
    /**
     * @param {string} key
     * @param {string} [defaultValue]
     * @returns {string|undefined} the stored value, or `defaultValue`
     */
    get(key, defaultValue = undefined) {
        throw new Error("Not implemented");
    }

    /**
     * @param {string} key
     * @param {string} value
     */
    set(key, value) {
        throw new Error("Not implemented");
    }

    /**
     * @param {string} key
     */
    remove(key) {
        throw new Error("Not implemented");
    }
}

/**
 * {@link StorageAdapter} backed by `document.cookie`, making the stored values
 * available to the server on the next request.
 */
export class CookieStorage extends StorageAdapter {
    // Preferences are meant to be kept "forever", the browser will cap it anyway.
    static maxAge = 10 * 365 * 24 * 60 * 60;
    static path = "/";

    /**
     * @param {Object} [options]
     * @param {number} [options.maxAge] cookies lifetime, in seconds
     * @param {string} [options.path]
     */
    constructor({ maxAge = CookieStorage.maxAge, path = CookieStorage.path } = {}) {
        super();
        this.maxAge = maxAge;
        this.path = path;
    }

    /**
     * @returns {Object<string, string>} all the readable cookies
     */
    get all() {
        const cookies = {};
        for (const cookie of document.cookie.split(";")) {
            const separator = cookie.indexOf("=");
            if (separator === -1) {
                continue;
            }
            const key = decodeURIComponent(cookie.slice(0, separator).trim());
            cookies[key] = decodeURIComponent(cookie.slice(separator + 1).trim());
        }
        return cookies;
    }

    get(key, defaultValue = undefined) {
        return this.all[key] ?? defaultValue;
    }

    set(key, value) {
        this._write(key, value, this.maxAge);
    }

    remove(key) {
        this._write(key, "", 0);
    }

    /**
     * @param {string} key
     * @param {string} value
     * @param {number} maxAge
     */
    _write(key, value, maxAge) {
        const parts = [
            `${encodeURIComponent(key)}=${encodeURIComponent(value)}`,
            `path=${this.path}`,
            `max-age=${maxAge}`,
        ];
        document.cookie = parts.join("; ");
    }
}
