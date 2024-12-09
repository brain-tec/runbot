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

const copyHashToClipboard = (hash) => {
    if (!navigator.clipboard) {
        return
    }
    navigator.clipboard.writeText(location.origin + location.pathname + `#${hash}`);
}

const switchTheme = (theme) => {
    document.documentElement.dataset.bsTheme = theme;
}

// setInterval(() => {
//     if (document.documentElement.dataset.bsTheme === 'dark') {
//         switchTheme('light');
//     } else {
//         switchTheme('dark');
//     }
// }, 2000)

const dark = switchTheme.bind(null, 'dark');
const legacy = switchTheme.bind(null, 'legacy');
const light = switchTheme.bind(null, 'light');
const red404 = switchTheme.bind(null, 'red404');

setTimeout(() => {
    const navbarElem = document.querySelector('nav.navbar');
    const toolbarElem = document.querySelector('.o_runbot_toolbar.position-sticky');

    if (navbarElem && toolbarElem) {
        toolbarElem.style.top = navbarElem.getBoundingClientRect().height;
        new ResizeObserver(() => {
            console.log('resize')
            toolbarElem.style.top = navbarElem.getBoundingClientRect().height;
        }).observe(navbarElem);
    }
}, 150);
