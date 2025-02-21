/** @odoo-module **/

import { TextField } from "@web/views/fields/text/text_field";
import { CharField } from "@web/views/fields/char/char_field";
import { Many2OneField } from "@web/views/fields/many2one/many2one_field";

import { _lt } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useDynamicPlaceholder } from "@web/views/fields/dynamic_placeholder_hook";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useInputField } from "@web/views/fields/input_field_hook";

import { useRef, xml, Component, onWillRender } from "@odoo/owl";
import { useAutoresize } from "@web/core/utils/autoresize";
import { getFormattedValue } from "@web/views/utils";

import { UrlField } from "@web/views/fields/url/url_field";

import { X2ManyField } from "@web/views/fields/x2many/x2many_field";
import { formatX2many } from "@web/views/fields/formatters";

import { BooleanToggleField } from "@web/views/fields/boolean_toggle/boolean_toggle_field";


function stringify(obj) {
    return JSON.stringify(obj, null, '\t')
}


export class JsonField extends TextField {
    static template = xml`
    <t t-if="props.readonly">
            <span t-out="value"/>
        </t>
        <t t-else="">
            <div t-ref="div">
                <textarea
                    class="o_input"
                    t-att-class="{'o_field_translate': props.isTranslatable}"
                    t-att-id="props.id"
                    t-att-placeholder="props.placeholder"
                    t-att-rows="rowCount"
                    t-ref="textarea"
                />
            </div>
        </t>
    `;
    setup() {
        this.divRef = useRef("div");
        this.textareaRef = useRef("textarea");
        //if (this.props.dynamicPlaceholder) {
        //    this.dynamicPlaceholder = useDynamicPlaceholder(this.textareaRef);
        //}

        useInputField({
            getValue: () => this.value,
            refName: "textarea",
            parse: JSON.parse,
        });
        useAutoresize(this.textareaRef, { minimumHeight: 50 });
    }
    get value() {
        return stringify(this.props.record.data[this.props.name] || "");
    }
}

registry.category("fields").add("runbotjsonb", {
    supportedTypes: ["jsonb"],
    component: JsonField,
});

export class FrontendUrl extends Component {
    static template = xml`
        <div><a t-att-href="route" target="_blank"><t t-out="displayValue"/></a></div>
    `;

    static components = { Many2OneField };

    static props = {
        ...Many2OneField.props,
        linkField: { type: String, optional: true },
    };

    get baseProps() {
        console.log(omit(this.props, 'linkField'))
        return omit(this.props, 'linkField', 'context')
    }

    get displayValue() {
        return this.props.record.data[this.props.name] ? getFormattedValue(this.props.record, this.props.name) : ''
    }

    get route() {
        return this._route(this.props.linkField || this.props.name)
    }

    _route(fieldName) {
        const model = this.props.record.fields[fieldName].relation || "runbot.unknown";
        const id = this.props.record.data[fieldName][0];
        if (model.startsWith('runbot.') ) {
            return '/runbot/' + model.split('.')[1] + '/' + id;
        } else {
            return false;
        }
    }
}

registry.category("fields").add("frontend_url", {
    supportedTypes: ["many2one"],
    component: FrontendUrl,
    extractProps({ attrs, options }, dynamicInfo) {
        return {
            linkField: options.link_field,
        };
    },
});


export class FieldCharFrontendUrl extends Component {

    static template = xml`
    <div class="o_field_many2one_selection">
        <div class="o_field_widget"><CharField t-props="props" /></div>
        <div><a t-att-href="route" target="_blank"><span class="fa fa-play ms-2"/></a></div>
    </div>`;

    static components = { CharField }

    get route() {
        const model = this.props.record.resModel;
        const id = this.props.record.resId;
        if (model.startsWith('runbot.') ) {
            return '/runbot/' + model.split('.')[1] + '/' + id;
        } else {
            return false;
        }
    }
}

registry.category("fields").add("char_frontend_url", {
    supportedTypes: ["char"],
    component: FieldCharFrontendUrl,
});


// Pull Request URL Widget
const pullRequestRegex = /\/([a-zA-Z-_]+\/[a-zA-Z-_]+)\/pull\/(\d+)/;
class PullRequestUrlField extends UrlField {
    static template = xml`
        <UrlField t-props="fieldProps"/>
    `;
    static components = { UrlField }
    get fieldProps() {
        const props = {...this.props};
        const parts = pullRequestRegex.exec(this.props.record.data[props.name])
        if (parts) {
            props.text = `${parts[1]}#${parts[2]}`;
        }
        return props
    }
}

PullRequestUrlField.supportedTypes = ["char"];


registry.category("fields").add("pull_request_url", {
    supportedTypes: ["char"],
    component: PullRequestUrlField,
});


//export class GithubTeamWidget extends CharField {

//this.value.split(',').forEach((value) => {
//    const href = 'https://github.com/orgs/' + organisation + '/teams/' + value.trim() + '/members';
//}
//
//registry.category("fields").add("github_team", GithubTeamWidget);




export class Matrixx2ManyField extends X2ManyField {
    static template = 'runbot.Matrixx2ManyField';
    static props = { ...standardFieldProps };

    static components = { BooleanToggleField };

    setup() {
        
        onWillRender(() => {
            this.initValues();
        })
    }

    getEntry(from, to) {
        const entry = this.entry_per_version[[from, to]];
        return entry
    }

    initValues() {
        this.data = this.props.record.data[this.props.name]
        this.to_versions = [];
        this.from_versions = [];
        this.entry_per_version = {};
        this.data.records.forEach((record) => {
            if (!this.to_versions.includes(record.data.to_version_number)) {
                this.to_versions.push(record.data.to_version_number)
            }
            if (!this.from_versions.includes(record.data.from_version_number)) {
                this.from_versions.push(record.data.from_version_number)
            }
            this.entry_per_version[[record.data.from_version_number, record.data.to_version_number]] = record;
        });
        console.log(this.to_versions)
        this.to_versions.sort()
        //this.to_versions.reverse()
        this.from_versions.sort()
        this.from_versions.reverse()
    }
}

export const matrixx2ManyField = {
    component: Matrixx2ManyField,
    useSubView: false,
};


registry.category("fields").add("version_matrix", matrixx2ManyField);
