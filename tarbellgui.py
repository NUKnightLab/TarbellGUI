import codecs
import glob
import imp
import jinja2
import multiprocessing
import os
import pkg_resources
import sh
import shutil
import sys
from tarbell.app import pprint_lines, process_xlsx, copy_global_values
from tarbell.configure import _get_or_create_config, _setup_default_templates
from tarbell.contextmanagers import ensure_project, ensure_settings
from tarbell.cli import _get_path, _get_project_title, _get_template, _mkdir
from tarbell.settings import Settings
from tarbell.slughifi import slugify
from tarbell import __VERSION__ as TARBELL_VERSION

try:
    import tkinter as tk
    from tkinter import *
except ImportError:
    import Tkinter as tk
    from Tkinter import *


def list_projects(projects_path):
    projects = []
    for directory in os.listdir(projects_path):
        project_path = os.path.join(projects_path, directory)
        try:
            filename, pathname, description = imp.find_module(
                'tarbell_config', [project_path])
            config = imp.load_module(directory, filename, pathname, description)
            projects.append((project_path, config))
        except ImportError:
                pass
    return projects


def install_requirements(path):
    """Install requirements.txt"""
    locations = [
        os.path.join(path, "_blueprint"),
        os.path.join(path, "_base"),
        path] 
    for location in locations:
        if os.path.isfile(os.path.join(location, 'requirements.txt')):
            pip = sh.pip.bake(_cwd=location)
            pip("install", "-r", "requirements.txt")


def copy_config_template(name, title, template, path, key, settings):
    """Get and render tarbell_config.py.template from blueprint"""
    context = settings.config
    context.update({
        "default_context": {
            "name": name,
            "title": title,
        },
        "name": name,
        "title": title,
        "template_repo_url": template.get('url'),
        "key": key,
    })
    if not key:
        xl_path = os.path.join(path, '_blueprint/', '_spreadsheet.xlsx')
        try:
            with open(xl_path, "rb") as f:
                data = process_xlsx(f.read())
                if 'values' in data:
                    data = copy_global_values(data)
                context["default_context"].update(data)
        except IOError:
            pass
    # TODO: S3 support
    template_dir = os.path.dirname(
        pkg_resources.resource_filename("tarbell",
        "templates/tarbell_config.py.template"))
    loader = jinja2.FileSystemLoader(template_dir)
    env = jinja2.Environment(loader=loader)
    env.filters["pprint_lines"] = pprint_lines  # For dumping context
    content = env.get_template('tarbell_config.py.template').render(context)
    codecs.open(os.path.join(path, "tarbell_config.py"), "w",
        encoding="utf-8").write(content)


def new_project(path, name, settings, title, template):
    key = None
    git = sh.git.bake(_cwd=path)
    git.init()
    if template.get('url'):
        git.submodule.add(template['url'], '_blueprint')
        git.submodule.update(*['--init'])
        submodule = sh.git.bake(_cwd=os.path.join(path, '_blueprint'))
        submodule.fetch()
        submodule.checkout(TARBELL_VERSION)
        files = glob.iglob(os.path.join(path, '_blueprint', '*.html'))
        for f in files:
            if os.path.isfile(f):
                dir_, filename = os.path.split(f)
                if not filename[0] in ('_', '.'):
                    shutil.copy2(f, path)
        ignore = os.path.join(path, '_blueprint', '.gitignore')
        if os.path.isfile(ignore):
            shutil.copy2(ignore, path)
    else:
        open(os.path.join(path, 'index.html', 'w'))
    copy_config_template(name, title, template, path, key, settings)
    git.add('.')
    git.commit(m='Created {0} from {1}'.format(name, template['name']))
    install_requirements(path)


class TarbellListbox(Listbox):

    def set_item_color(self, index, color):
        self.itemconfig(index,
            { 'fg':color, 'selectforeground':color })

    def append(self, item):
        self.insert(END, item)


class TarbellApp(object):

    def __init__(self, master):
        self._projects_path = None
        self.root = master
        self.p = None
        self.frm = Frame(master)
        self.layout()
        self.frm.pack(fill=BOTH)
        self.active_index = None
        self.active_project = None
        self.active_project_state = None

    def _run_server(self, project_path):
        with ensure_project('serve', [], path=project_path) as site:
            self.site = site
            site.app.run('0.0.0.0', port=5000, use_reloader=False)

    def run_server(self, project_path):
        self.p = multiprocessing.Process(target=self._run_server,
            args=(project_path,))
        self.p.start()

    def stop_server(self):
        if self.p:
            self.p.terminate()

    def destroy(self):
        self.stop_server()
        self.root.quit()

    def get_template(self):
        return {
            'url': 'https://github.com/newsapps/tarbell-template',
            'name': 'Basic Bootstrap 3 template'
        }

    def _create_project(self, settings):
        title = self.new_project_var.get()
        name = slugify(unicode(title))
        path = os.path.join(settings.config.get('projects_path'), name)
        _mkdir(path)
        new_project(path, name, settings, title, self.get_template())
        self.project_listbox.append(path)

    def create_project(self):
        with ensure_settings('create', []) as settings:
            self._create_project(settings)

    def projects_path(self):
        if self._projects_path is None:
            with ensure_settings('list', []) as settings:
                self._projects_path = settings.config.get("projects_path")
        return self._projects_path

    def _layout_create_frame(self):
        create_frame = Frame(self.frm, bg='green')
        self.new_project_var = StringVar()
        new_project_entry = Entry(create_frame,
            textvariable=self.new_project_var)
        new_project_button = Button(create_frame, text="Create Project",
            command=self.create_project)
        new_project_entry.pack(side=LEFT, fill=X, expand=YES)
        new_project_button.pack(side=RIGHT)
        create_frame.pack(fill=X)

    def _layout_project_list(self):
        projects_frame = Frame(self.frm, bg='red')
        scrollbar = Scrollbar(projects_frame)
        listbox = TarbellListbox(projects_frame, yscrollcommand=scrollbar.set)
        projects_frame.pack(fill=BOTH)
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(fill=Y, side=RIGHT)
        for project in list_projects(self.projects_path()):
            listbox.append(project[0])
        listbox.bind('<<ListboxSelect>>', self.project_select)
        listbox.pack(side=LEFT, fill=BOTH, expand=YES)
        self.project_listbox = listbox

    def _layout_buttons(self):
        button_frame = Frame(self.frm)
        self.action_button = Button(button_frame, text='...',
            command=self.action, width=5, state='disabled')
        self.config_button = Button(button_frame, text='Configure',
            command=self.config, state='disabled')
        self.settings_button = Button(button_frame, text='Settings',
            command=self.settings)
        self.action_button.pack(side=LEFT)
        self.config_button.pack(side=LEFT)
        self.settings_button.pack(side=RIGHT)
        button_frame.pack(side=BOTTOM, fill=X)

    def layout(self):
        self._layout_create_frame()
        self._layout_project_list()
        self._layout_buttons()

    def project_select(self, event):
        selected = self.project_listbox.selection_get()
        if selected:
            self.config_button.config(state='normal')
        if selected == self.active_project:
            if self.active_project_state == 'running':
                self.action_button.config(text='Stop', state='normal')
            else:
                self.action_button.config(text='Run', state='normal')
        else:
            self.action_button.config(text='Switch', state='normal')

    def action(self):
        action_ = self.action_button.config('text')[-1]
        selected = self.project_listbox.selection_get()
        if action_ == 'Switch':
            self.stop_server()
            self.run_server(selected)
            self.action_button.config(text='Stop') 
            index = int(self.project_listbox.curselection()[0])
            if self.active_index:
                self.project_listbox.set_item_color(self.active_index, 'black')
            self.project_listbox.set_item_color(index, 'forest green')
            self.active_index = index
            self.active_project = selected
            self.active_project_state = 'running'
        elif action_ == 'Run':
            self.run_server(selected)
            self.active_project_state = 'running'
        elif action_ == 'Stop':
            self.stop_server()
            if self.active_index:
                self.project_listbox.set_item_color(self.active_index, 'black')
            self.active_project_state = 'stopped'
            self.action_button.config(text='Run')

    def config(self):
        pass

    def settings(self):
        pass

    @classmethod
    def ensure_config(cls):
        settings = Settings()
        path = settings.path
        if not os.path.isfile(path):
            projects_path = os.path.expanduser(os.path.join("~", "tarbell"))
            if not os.path.exists(projects_path):
                os.makedirs(projects_path)
            config = _get_or_create_config(path, prompt=False)
            settings.config.update({"projects_path": projects_path})
            settings.config.update(_setup_default_templates(settings,
                settings.path, prompt=False))
            settings.config.update({'s3_credentials': {}});
            with open(path, 'w') as f:
                settings.save()

    @classmethod
    def run(cls):
        cls.ensure_config()
        root = Tk()
        root.title('Tarbell')
        root.option_add('*font', ('verdana', 12, 'bold'))
        app = TarbellApp(root)
        root.protocol('WM_DELETE_WINDOW', app.destroy)
        root.mainloop()


if __name__ == "__main__":
    TarbellApp.run()

