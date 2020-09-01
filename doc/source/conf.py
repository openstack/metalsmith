# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys

# Add the project
sys.path.insert(0, os.path.abspath('../..'))
# Add the extensions
sys.path.insert(0, os.path.join(os.path.abspath('.'), '_exts'))
# -- General configuration ----------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = [
    'sphinxcontrib.apidoc',
    'sphinxcontrib.rsvgconverter',
    'openstackdocstheme',
    'ansible-autodoc'
]

autoclass_content = 'both'
apidoc_module_dir = '../../metalsmith'
apidoc_output_dir = 'reference/api'
apidoc_excluded_paths = ['test']
apidoc_separate_modules = True

# autodoc generation is a bit aggressive and a nuisance when doing heavy
# text edit cycles.
# execute "export SPHINX_DEBUG=1" in your terminal to disable

# The suffix of source filenames.
source_suffix = '.rst'

# The master toctree document.
master_doc = 'index'

# General information about the project.
copyright = u'2018, MetalSmith Developers '

# If true, '()' will be appended to :func: etc. cross-reference text.
add_function_parentheses = True

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = True

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = 'native'

openstackdocs_repo_name = 'openstack/metalsmith'
openstackdocs_bug_project = 'metalsmith'
openstackdocs_bug_tag = ''

# -- Options for HTML output --------------------------------------------------

# The theme to use for HTML and HTML Help pages.  Major themes that come with
# Sphinx are currently 'default' and 'sphinxdoc'.
# html_theme_path = ["."]
# html_theme = '_theme'
# html_static_path = ['static']

html_theme = 'openstackdocs'

# Output file base name for HTML help builder.
htmlhelp_basename = 'metalsmithdoc'

latex_use_xindy = False

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title, author, documentclass
# [howto/manual]).
latex_documents = [
    ('index',
     'doc-metalsmith.tex',
     u'MetalSmith Documentation',
     u'MetalSmith Developers', 'manual'),
]

# Example configuration for intersphinx: refer to the Python standard library.
# intersphinx_mapping = {'http://docs.python.org/': None}
