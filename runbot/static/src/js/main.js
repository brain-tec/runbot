const { Component } = owl;
const { xml } = owl.tags;
const { whenReady } = owl.utils;
const { useState } = owl.hooks;

get_color_class = function(build) {

    if (build.global_result == 'ko')
        return 'danger'
    if (build.global_result == 'warn')
        return 'warning'

    if (build.global_state == 'pending')
        return 'default'
    if (('testing', 'waiting').indexOf(build.global_state) != -1)
        return 'info'

    if (build.global_result == 'ok')
        return 'success'

    if (('skipped', 'killed', 'manually_killed').indexOf(build.global_result) != -1)
        return 'killed'

}

    
const LOAD_INFOS_TEMPLATE = xml /* xml */`
<span>
    <span t-attf-class="badge badge-{{load_infos.pending_level}}">
        Pending:
        <t t-esc="load_infos.pending_total"/>
    </span>
    <t t-set="klass">success</t>
    <t t-if="! load_infos.workers" t-set="klass">danger</t>
    <t t-else="">
        <t t-if="load_infos.testing/load_infos.workers > 0" t-set="klass">info</t>
        <t t-if="load_infos.testing/load_infos.workers > 0.75" t-set="klass">warning</t>
        <t t-if="load_infos.testing/load_infos.workers >= 1" t-set="klass">danger</t>
    </t>
    <span t-attf-class="badge badge-{{klass}}">
        Testing:
        <t t-esc="load_infos.testing"/>
        /
        <t t-esc="load_infos.workers"/>
    </span>
</span>
`;

class LoadInfos extends Component {
    static template = LOAD_INFOS_TEMPLATE;
    willStart() {
        this.load_infos = this.props.load_infos
    }
}

const COPY_BUTTON_TEMPLATE = xml /* xml */`
<button t-attf-class="btn btn-default btn-ssm" title="Copy Bundle name" aria-label="Copy Bundle name">
    <i t-attf-class="fa fa-clipboard"/>
</button>
    `;

class CopyButton extends Component {
    static template = COPY_BUTTON_TEMPLATE;
    willStart() {
        this.bundle = this.props.bundle
    }
    copyToClipboard() {}

    //todo t-attf-onclick="copyToClipboard('{{ bundle.name.split(':')[-1] }}')
}
       

const BUILD_MENU_TEMPLATE = `
<t>
    <button t-attf-class="btn btn-default dropdown-toggle" data-toggle="dropdown" title="Build options" aria-label="Build options" aria-expanded="false">
        <i t-attf-class="fa {{build.global_state == 'pending' ? 'fa-spinner' : 'fa-cog'}} {{('done', 'running').indexOf(build.global_state) == -1 ? '' : 'fa-spin'}} fa-fw"/>
        <span class="caret"/>
    </button>
    <div class="dropdown-menu dropdown-menu-right" role="menu">
        <a t-if="build.global_result=='skipped'" groups="runbot.group_runbot_admin" class="dropdown-item" href="#" data-runbot="rebuild" t-att-data-runbot-build="build.id">
            <i class="fa fa-level-up"/>
            Force Build
        </a>
        <t t-if="build.local_state=='running'">
            <a class="dropdown-item" t-attf-href="http://{{build.domain}}/?db={{build.dest}}-all">
                <i class="fa fa-sign-in"/>
                Connect all
            </a>
            <a class="dropdown-item" t-attf-href="http://{{build.domain}}/?db={{build.dest}}-base">
                <i class="fa fa-sign-in"/>
                Connect base
            </a>
            <a class="dropdown-item" t-attf-href="http://{{build.domain}}/">
                <i class="fa fa-sign-in"/>
                Connect
            </a>
        </t>
        <a class="dropdown-item" t-if="['done','running'].indexOf(build.global_state) !== -1 or requested_action == 'deathrow'" groups="base.group_user" href="#" data-runbot="rebuild" t-att-data-runbot-build="build.id" title="Retry this build, usefull for false positive">
            <i class="fa fa-refresh"/>
            Rebuild
        </a>
        <t t-if="build.global_state != 'done'">
            <t t-if="build.requested_action != 'deathrow'">
                <a groups="base.group_user" href="#" data-runbot="kill" class="dropdown-item" t-att-data-runbot-build="build.id">
                    <i class="fa fa-crosshairs"/>
                    Kill
                </a>
            </t>
            <t t-else="">
                <a groups="base.group_user" data-runbot="kill" class="dropdown-item disabled">
                    <i class="fa fa-spinner fa-spin"/>
                    Killing
                    <i class="fa fa-crosshairs"/>
                </a>
            </t>
        </t>
        <t t-if="build.global_state == 'done'">
            <t t-if="build.requested_action != 'wake_up'">
                <a groups="base.group_user" class="dropdown-item" href="#" data-runbot="wakeup" t-att-data-runbot-build="build.id">
                    <i class="fa fa-coffee"/>
                    Wake up
                </a>
            </t>
            <t t-else="">
                <a groups="base.group_user" class="dropdown-item disabled" data-runbot="wakeup">
                    <i class="fa fa-spinner fa-spin"/>
                    Waking up
                    <i class="fa fa-crosshairs"/>
                </a>
            </t>
        </t>
        <div t-if="('testing', 'waiting', 'pending').indexOf(build.global_state) != -1" groups="base.group_user" class="dropdown-divider"/>
        <t t-if="build.log_list">
            <t t-foreach="build.log_list.split(',')" t-as="log_name" t-key="log_name">
                <a class="dropdown-item" t-attf-href="http://{{build.host}}/runbot/static/build/{{build.dest}}/logs/{{log_name}}.txt">
                    <i class="fa fa-file-text-o"/>
                    Full
                    <t t-esc="log_name"/>
                    logs
                </a>
            </t>
        </t>
        <t groups="runbot.group_runbot_admin">
            <div class="dropdown-divider"/>
            <a class="dropdown-item" t-attf-href="/web/#id={{build.id}}&amp;view_type=form&amp;model=runbot.build" target="new">
                <i class="fa fa-list"/>
                View in backend
            </a>
        </t>
    </div>
</t>`;


/*class BuildMenu extends Component {
    static template = BUILD_MENU_TEMPLATE;
    willStart() {
        this.build=this.props.build;
    }
}*/


const SLOT_BUTTON_TEMPLATE = xml /* xml */`
<div t-attf-class="btn-group btn-group-ssm slot_button_group">
    <span t-attf-class="btn btn-{{color}} disabled" t-att-title="slot.link_type">
        <i t-attf-class="fa fa-{{slot.fa_link_type}}"/>
    </span>
    <a t-if="build" t-attf-href="/runbot/build/{{build.id}}" t-attf-class="btn btn-default slot_name">
        <span t-esc="slot.trigger_id.name"/>
    </a>
    <span t-else="" t-attf-class="btn btn-default disabled slot_name">
        <span t-esc="slot.trigger_id.name"/>
    </span>
    <a t-if="build.local_state == 'running'" t-attf-href="http://{{build.domain}}/" class="fa fa-sign-in btn btn-info"/>
    <t t-call="BuildMenu"/>
    <a t-if="! build" class="btn btn-default" title="Create build" t-attf-href="/runbot/batch/slot/{{slot.id}}/build">
        <i class="fa fa-play fa-fw"/>
    </a>
</div>
    `;

class SlotButton extends Component {
    static template = SLOT_BUTTON_TEMPLATE;
    willStart() {
        this.slot = this.props.slot
        this.build = this.slot.build_id;
        this.color = get_color_class(this.build);
    }
}

const BATCH_TILE_TEMPLATE = xml /* xml */`
<div t-attf-class="batch_tile {{more? 'more' : 'nomore'}}">
    <div t-attf-class="card bg-{{klass}}-light">
        <div class="batch_header">
            <a t-attf-href="/runbot/batch/{{batch.id}}" t-attf-class="badge badge-{{batch.has_warning ? 'warning' : 'light'}}" title="View Batch">
                <t t-esc="batch.formated_age"/>
                <i class="fa fa-exclamation-triangle" t-if="batch.has_warning"/>
                <i class="arrow fa fa-window-maximize"/>
            </a>
        </div>
        <t t-if="batch.state=='preparing'">
            <span><i class="fa fa-cog fa-spin fa-fw"/> preparing</span>
        </t>
        <div class="batch_slots">
            <t t-foreach="displayableSlots" t-as="slot" t-key="slot.id">
                <SlotButton class="slot_container" slot="slot"/>
            </t>
            <div class="slot_filler" t-foreach="Array(10).keys()" t-as="x"/>
        </div>
        <div class="batch_commits">
            <div t-foreach="commit_links" t-as="commit_link" class="one_line" t-key="commit_link.id">
                <a t-attf-href="/runbot/commit/{{commit_link.commit_id}}" t-attf-class="badge badge-light batch_commit match_type_{{commit_link.match_type}}">
                <i class="fa fa-fw fa-hashtag" t-if="commit_link.match_type == 'new'" title="This commit is a new head"/>
                <i class="fa fa-fw fa-link" t-if="commit_link.match_type == 'head'" title="This commit is an existing head from bundle branches"/>
                <i class="fa fa-fw fa-code-fork" t-if="commit_link.match_type == 'base_match'" title="This commit is matched from a base batch with matching merge_base"/>
                <i class="fa fa-fw fa-clock-o" t-if="commit_link.match_type == 'base_head'" title="This commit is the head of a base branch"/>
                <t t-esc="commit_link.commit_dname"/>
                </a>
                <a t-att-href="'https://%s/commit/%s' % (commit_link.commit_remote_url, commit_link.commit_name)" class="badge badge-light" title="View Commit on Github"><i class="fa fa-github"/></a>
                <span t-esc="commit_link.commit_subject"/>
            </div>
        </div>
        
        
    </div>
</div>`;

class BatchTile extends Component {
    static template = BATCH_TILE_TEMPLATE;
    static components = { SlotButton };
    static trigger_display = false; // TODO
    //  <t t-if=="()">
    willStart() {
        this.more = false; // TODO
        this.batch = this.props.batch
        this.klass = "info";
        this.trigger_display = null; // TODO
        this.displayableSlots = this.batch.slot_ids.filter(slot => slot.build_id.id && !slot.trigger_id.manual && ((! slot.trigger_id.hide && this.trigger_display === null) || (this.trigger_display && this.trigger_display.indexOf(slot.trigger_id.id) != -1)));
        //

        this.commit_links = [...this.batch.commit_link_ids]
        this.commit_links.sort(cl => (cl.commit_repo_sequence, cl.commit_repo_id))
        if (this.batch.state == "skipped") {
            this.klass = "killed";
        } else if (this.batch.state=='done') {
            console.log(this.batch)
            if (this.batch.slot_ids.every((slot) => ! slot.build_id.id || slot.build_id.global_result == 'ok')) {
                this.klass = "success";
            } else {
                this.klass = "danger";
            }
        }
    }

}

const BUNDLES_TEMPLATE = xml /* xml */`
<div id="wrapwrap">
    <header>
        <nav class="navbar navbar-expand-md navbar-light bg-light">
            <a t-attf-href="/runbot/{{project.slug}}">
                <b style="color:#777;">
                    <t t-esc="project.name"/>
                </b>
            </a>
            <button type="button" class="navbar-toggler" data-toggle="collapse" data-target="#top_menu_collapse">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="top_menu_collapse" aria-expanded="false">
                <ul class="nav navbar-nav ml-auto text-right" id="top_menu">
                    <li class="nav-item" t-foreach="projects" t-as="project">
                        <a class="nav-link" t-attf-href="/runbot/{{project.slug}}">
                            <t t-esc="project.name"/>
                        </a>
                    </li>
                        
                    <li class="nav-item divider"></li>
                    <t t-if="user">
                        <t t-if="user.public">
                            <li class="nav-item dropdown">
                                <b>
                                    <a class="nav-link" t-attf-href="/web/login?redirect=/">Login</a>
                                </b>
                            </li>
                        </t>
                        <t t-else="">
                            <t t-if="nb_assigned_errors and nb_assigned_errors > 0">
                                <li class="nav-item divider"/>
                                <li class="nav-item">
                                    <a href="/runbot/errors" class="nav-link text-danger" t-attf-title="You have {{nb_assigned_errors}} random bug assigned">
                                        <i class="fa fa-bug"/><t t-esc="nb_assigned_errors"/>
                                    </a>
                                </li>
                            </t>
                            <t t-elif="nb_build_errors and nb_build_errors > 0">
                                <li class="nav-item divider"/>
                                <li class="nav-item">
                                    <a href="/runbot/errors" class="nav-link" title="Random Bugs"><i class="fa fa-bug"/></a>
                                </li>
                            </t>
                            <li class="nav-item dropdown">
                                <a href="#" class="nav-link dropdown-toggle" data-toggle="dropdown">
                                    <b>
                                        <span t-esc=" user.name.length &gt; 25 ? user.namesubstring(0, 23) + '...' : user.name"/>
                                    </b>
                                </a>
                                <div class="dropdown-menu js_usermenu" role="menu">
                                    <a class="dropdown-item" id="o_logout" role="menuitem" t-attf-href="/web/session/logout?redirect=/">Logout</a>
                                    <a class="dropdown-item" role="menuitem" t-attf-href="/web">Web</a>
                                </div>
                            </li>
                        </t>
                    </t>
                    <li class="nav-item"><a class="nav-link" href="/doc">FAQ</a></li><!--todo this is a custom xpath-->
                </ul>
                    
                <form class="form-inline my-2 my-lg-0" role="search" method="get" action="/runbot/r-d-1">
                    <div class="input-group md-form form-sm form-2 pl-0">
                        <input class="form-control my-0 py-1 red-border" type="text" placeholder="Search" aria-label="Search" name="search" value=""/>
                        <div class="input-group-append" data-oe-model="ir.ui.view" data-oe-id="1455" data-oe-field="arch" data-oe-xpath="/t[1]/t[1]/t[1]/form[1]/div[1]/div[1]">
                            <button type="submit" class="input-group-text red lighten-3" id="basic-text1">
                                <i class="fa fa-search text-grey"></i>
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        </nav>
    </header>


    <div class="container-fluid frontend">
        <div class="row">
            <div class='col-md-12'>
                <LoadInfos load_infos="load_infos" t-if="load_infos"/>
            </div>
            <div class='col-md-12'>
                <div t-if="message" class="alert alert-warning" role="alert">
                    <t t-esc="message" /> <!-- todo fixme-->
                </div>
                <div t-if="! project" class="mb32">
                    <h1>No project</h1>
                </div>
                <div t-else="">
                    <div t-foreach="bundles" t-as="bundle" class="row bundle_row">
                        <div class="col-md-3 col-lg-2 cell">
                            <div class="one_line">
                                <i t-if="bundle.sticky" class="fa fa-star" style="color: #f0ad4e" />
                                <a t-attf-href="/runbot/bundle/{{bundle.id}}" title="View Bundle">
                                <b t-esc="bundle.name"/>
                                </a>
                            </div>
                            <div class="btn-toolbar" role="toolbar" aria-label="Toolbar with button groups">
                                <div class="btn-group" role="group">
                                <t t-foreach="categories" t-as="category" t-key="category.id">
                                    <t t-if="active_category_id != category.id">
                                        <t t-set="last_category_batch_id" t-value="bundle.last_category_batch[category.id]"/>
                                        <t t-if="last_category_batch_id">
                                            <t t-if="category.view_id" t-call="{{category.view_id.key}}"/>
                                            <a t-else=""
                                            t-attf-title="View last {{category.name}} batch"
                                            t-attf-href="/runbot/batch/{{last_category_batch_id}}"
                                            t-attf-class="fa fa-{{category.icon}}"
                                            />
                                        </t>
                                    </t>
                                </t>
                                </div>
                                <div class="btn-group" role="group">
                                    <CopyButton t-if="!bundle.sticky" bundle="bundle"/>
                                    <!--t t-call="runbot.branch_github_menu"/-->
                                </div>
                            </div>
                        </div>
                        <div class="col-md-9 col-lg-10">
                            <div class="row no-gutters">
                                <div t-foreach="bundle.last_batchs" t-as="batch" t-attf-class="col-md-6 col-xl-3 {{batch_index > 1 ? 'd-none d-xl-block' : ''}}" t-key="batch.id">
                                    <BatchTile batch="batch"/>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>`;

class App extends Component {
    static template = BUNDLES_TEMPLATE;
    static components = { CopyButton, BatchTile, LoadInfos };
    sticky_bundle = useState([]);
    project = base_data.project
    projects = base_data.projects
    user = base_data.user;
    bundles = useState([]);

    willStart() {
        const xhttp = new XMLHttpRequest();
        const self = this;
        if (this.project) {
            xhttp.onreadystatechange = function() {
                if (this.readyState == 4 && this.status == 200) {
                    const res = JSON.parse(this.responseText);
                    const bundles = res.result.bundles;
                    // no so usefull but update known data, especially user (bu also project/projects/categories)
                    self.user = res.result.user;
                    self.project = res.result.project;
                    self.projects = res.result.projects;
                    self.categories = res.result.categories;
                    self.active_category_id = res.result.active_category_id;
                    self.load_infos = res.result.load_infos
                    res.result.bundles.forEach(bundle => self.bundles.push(bundle));
                }
            };
            xhttp.open("POST", "/runbot/data/bundles/1/" + this.project.id);
            xhttp.setRequestHeader('Content-Type', 'application/json');
            xhttp.send(JSON.stringify({}));
        }
    }
}

async function setup() {
    owl.config.mode = "dev"; // TODO remove
    const app = new App();
    app.env.qweb.addTemplate('BuildMenu', BUILD_MENU_TEMPLATE);
    app.mount(document.body);
}

whenReady(setup);