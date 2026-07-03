import { evaluateExpr } from "@web/core/py_js/py";

/**
 * Client-side lightweight QWeb rendering engine.
 * @param {DocumentFragment|HTMLElement} rootNode - The template fragment or element to render.
 * @param {Object} context - The data context (parsed from the element's dataset).
 * @returns {DocumentFragment|HTMLElement} The rendered DOM node.
 */
export function render(rootNode, context) {
    // --- LOOPS (t-foreach / t-as) ---
    // Loops must be processed first so that individual nodes exist before evaluating their conditions.
    for (const loopEl of [...rootNode.querySelectorAll("[t-foreach]")]) {
        const arrayExpr = loopEl.getAttribute("t-foreach");
        const itemKey = loopEl.getAttribute("t-as") || "item";

        // Evaluate the expression via py_js to retrieve the target array
        const arrayData = evaluateExpr(arrayExpr, context);

        if (Array.isArray(arrayData)) {
            for (const [index, subItem] of arrayData.entries()) {
                const liClone = loopEl.cloneNode(true);
                liClone.removeAttribute("t-foreach");
                liClone.removeAttribute("t-as");

                // Create a sub-context for the current iteration (inherits parent context + loop variable + index)
                const loopContext = {
                    ...context,
                    [itemKey]: subItem,
                    [`${itemKey}_index`]: index,
                };

                // Recursive call to render conditions and bindings inside this specific looped node
                render(liClone, loopContext);

                loopEl.parentNode.insertBefore(liClone, loopEl);
            }
        }

        // Remove the original template node that served as the blueprint
        loopEl.remove();
    }

    // --- CONDITIONS (t-if / t-else) ---
    for (const ifEl of [...rootNode.querySelectorAll("[t-if]")]) {
        const expression = ifEl.getAttribute("t-if");
        const conditionMet = evaluateExpr(expression, context);

        // Check if the immediately following sibling is an "else" block
        const nextEl = ifEl.nextElementSibling;
        const hasElse = nextEl && nextEl.hasAttribute("t-else");

        if (conditionMet) {
            ifEl.removeAttribute("t-if");
            if (hasElse) {
                nextEl.remove();
            } // IF is true -> destroy the adjacent ELSE block
        } else {
            if (hasElse) {
                nextEl.removeAttribute("t-else"); // IF is false -> keep the ELSE block (and clean up its attribute)
            }
            ifEl.remove(); // Destroy the IF block
        }
    }

    // Remove any orphaned t-else blocks
    for (const el of [...rootNode.querySelectorAll("[t-else]")]) {
        el.remove();
    }

    // --- INTERPOLATION {{expression}} ---

    /**
     * Helper function to replace all {{...}} expressions within a string.
     * @param {string} str - The string to interpolate.
     * @returns {string}
     */
    const interpolate = (str) => {
        if (!str || !str.includes("{{")) {
            return str;
        }
        return str.replace(/\{\{\s*(.+?)\s*\}\}/g, (_, expr) => {
            const val = evaluateExpr(expr, context);
            return val !== undefined && val !== null ? val : "";
        });
    };

    // Attribute interpolation (e.g., href="{{item.url}}", class="btn {{item.class}}", data-val="{{item.id}}")
    // We collect the root node and all descendants to check every tag's attributes
    const elements = [rootNode, ...rootNode.querySelectorAll("*")];
    for (const el of elements) {
        if (!el.attributes) {
            continue;
        }
        for (const attr of [...el.attributes]) {
            if (attr.value.includes("{{")) {
                el.setAttribute(attr.name, interpolate(attr.value));
            }
        }
    }

    // Text node and tag content interpolation (e.g., <span>Hello {{item.title}}</span> or <div>{{item.desc}}</div>)
    // The TreeWalker naturally visits every text node embedded inside any tag structure
    const walker = document.createTreeWalker(rootNode, NodeFilter.SHOW_TEXT, null, false);
    let textNode;
    while ((textNode = walker.nextNode())) {
        if (textNode.nodeValue.includes("{{")) {
            textNode.nodeValue = interpolate(textNode.nodeValue);
        }
    }

    return rootNode;
}
