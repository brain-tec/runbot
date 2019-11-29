(function($) {
    "use strict";

    // classes should be replaced by e.g.
    var OPMAP = {
        'rebuild': {operation: 'force', then: 'redirect'},
        'rebuild-exact': {operation: 'force/1', then: 'redirect'},
        'kill': {operation: 'kill', then: 'reload'}, // or ignore?
        'wakeup': {operation: 'wakeup', then: 'redirect'}
    };

    $(function () {
        $(document).on('click', '[data-runbot]', function (e) {
            e.preventDefault();

            var $this = $(this);
            var segment = OPMAP[$this.data('runbot')];
            if (!segment) { return; }

            // no responseURL on $.ajax so use native object
            var xhr = new XMLHttpRequest();
            xhr.addEventListener('load', function () {
                switch (segment.then) {
                case 'redirect':
                    if (xhr.responseURL) {
                        window.location.href = xhr.responseURL;
                        break;
                    }
                // fallthrough to reload if no responseURL (MSIE)
                case 'reload':
                    window.reload();
                    break;
                }
            });
            xhr.open('POST', _.str.sprintf('/runbot/build/%s/%s', $this.data('runbot-build'), segment.operation));
            xhr.send();
        });
    });

    $(function() {
      new Clipboard('.clipbtn');
    });
})(jQuery);
