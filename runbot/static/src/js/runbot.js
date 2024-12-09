import publicWidget from "@web/legacy/js/public/public_widget";


publicWidget.registry.RunbotPage = publicWidget.Widget.extend({
    // This selector should not be so broad.
    selector: 'body',
    events: {
        'click [data-runbot]': '_onClickDataRunbot',
        'click [data-runbot-clipboard]': '_onClickRunbotCopy',
    },

    _onClickDataRunbot: async (event) => {
        const { currentTarget: target } = event;
        if (!target) {
            return;
        }
        event.preventDefault();
        const { runbot: operation, runbotBuild } = target.dataset;
        if (!operation) {
            return;
        }
        let url = target.href;
        if (runbotBuild) {
            url = `/runbot/build/${runbotBuild}/${operation}`
        }
        const response = await fetch(url, {
            method: 'POST',
        });
        if (operation == 'rebuild' && window.location.href.split('?')[0].endsWith(`/build/${runbotBuild}`)) {
            window.location.href = window.location.href.replace('/build/' + runbotBuild, '/build/' + await response.text());
        } else if (operation == 'action') {
            target.parentElement.innerText = await response.text();
        } else {
            window.location.reload();
        }
    },

    _onClickRunbotCopy: ({ currentTarget: target }) => {
        if (!navigator.clipboard || !target) {
            return;
        }
        navigator.clipboard.writeText(
            target.dataset.runbotClipboard
        );
    }
});

publicWidget.registry.ThemeSwitcher = publicWidget.Widget.extend({
    selector: '.o_runbot_theme_switcher',
    events: {
        'click .btn[data-theme]': '_onClickSwitchTheme',
    },

    _onClickSwitchTheme: ({ currentTarget: target }) => {
        document.documentElement.dataset.bsTheme = target.dataset.theme;
    }
});

publicWidget.registry.RunbotToolbar = publicWidget.Widget.extend({
    selector: '.o_runbot_toolbar.position-sticky',

    start: function () {
        this._super();

        const navbarElem = document.querySelector('nav.navbar');
        if (!navbarElem) {
            return;
        }
        this.resizeObserver = new ResizeObserver(() => {
            this.el.style.top = navbarElem.getBoundingClientRect().height;
        });
        this.resizeObserver.observe(this.el);
    },

    destroy: function () {
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
        }
        this._super();
    }
})

const copyHashToClipboard = (hash) => {
    if (!navigator.clipboard) {
        return
    }
    navigator.clipboard.writeText(location.origin + location.pathname + `#${hash}`);
}
