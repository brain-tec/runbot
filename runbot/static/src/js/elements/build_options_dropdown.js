import { render } from "../templating";
import { BaseButton } from "./base_button";

const lazyInteractionEvents = ["mouseenter", "focus", "touchstart", "click"];

function extractDataset(el) {
    const dataset = {};
    for (const { name } of [...el.attributes]) {
        if (name.startsWith("data-")) {
            const rawValue = el.getAttribute(name);
            let value;
            try {
                value = JSON.parse(rawValue);
            } catch {
                value = rawValue;
            }
            dataset[name.slice(5)] = value;
        }
    }
    return dataset;
}

class BuildOptionsDropdown extends BaseButton {
    constructor() {
        super();
        this.template = document.getElementById("build-options-dropdown-menu");
        this.data = extractDataset(this);
    }

    connectedCallback() {
        super.connectedCallback();
        this.classList.add("dropdown-toggle");
        this.setAttribute("data-bs-toggle", "dropdown");
        this.setAttribute("aria-expanded", "false");
        const lazyBuildMenuHandler = () => {
            if (this.nextElementSibling?.classList.contains("dropdown-menu")) {
                return;
            }
            const renderedMenu = render(this.template.content.cloneNode(true), this.data);
            this.after(renderedMenu);
            for (const eventType of lazyInteractionEvents) {
                this.removeEventListener(eventType, lazyBuildMenuHandler);
            }
        } ;
        for (const eventType of lazyInteractionEvents) {
            this.addEventListener(eventType, lazyBuildMenuHandler, { once: true });
        }
    }
}

customElements.define("build-options-dropdown", BuildOptionsDropdown);
