import { registry } from "@web/core/registry";
import { Interaction } from "@web/public/interaction";

class Runbot extends Interaction {
    static selector = ".runbot-public";
    dynamicContent = {
        "[data-runbot]": {
            "t-on-click.prevent": this.onClickDataRunbot,
        },
        "[data-clipboard-copy]": {
            "t-on-click.prevent": this.onClickClipboardCopy
        }
    };

    /**
     * @param {Event} ev
     */
    async onClickDataRunbot({ currentTarget }) {
        const { runbot: operation, runbotBuild } = currentTarget.dataset;
        if (!operation) {
            return;
        }
        let url = currentTarget.href;
        if (runbotBuild) {
            url = `/runbot/build/${runbotBuild}/${operation}`;
        }
        const response = await fetch(url, {
            method: "POST",
        });
        const responseText = await response.text();
        if (operation === "rebuild" && window.location.pathname.endsWith(`/build/${runbotBuild}`)) {
            const currentURL = new URL(window.location.href);
            currentURL.pathname = `/build/${responseText}`;
            window.location.href = currentURL.toString();
        } else if (operation === "action") {
            currentTarget.parentElement.innerText = responseText;
        } else {
            window.location.reload();
        }
    }

    /**
     * @param {Event} ev
     */
    async onClickClipboardCopy({ currentTarget }) {
        navigator.clipboard.writeText(currentTarget.dataset.clipboardCopy);
    }
}

registry.category("public.interactions").add("runbot", Runbot);
