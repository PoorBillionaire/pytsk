#!/usr/bin/python
#
# Copyright 2010, Michael Cohen <scudette@gmail.com>.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Install the pytsk python module.

You can control the installation process using the following environment
variables:

SLEUTHKIT_SOURCE: The path to the locally downloaded tarball of the
  sleuthkit. If not specified we download from the internet.

SLEUTHKIT_PATH: A path to the locally build sleuthkit source tree. If not
  specified we use SLEUTHKIT_SOURCE environment variable (above).

"""

from __future__ import print_function

import glob
import re
import os
import subprocess
import sys
import time

import distutils.ccompiler

from distutils.ccompiler import new_compiler
from setuptools import setup, Command, Extension
from setuptools.command.build_ext import build_ext
from setuptools.command.sdist import sdist

try:
  from distutils.command.bdist_msi import bdist_msi
except ImportError:
  bdist_msi = None

try:
  from distutils.command.bdist_rpm import bdist_rpm
except ImportError:
  bdist_rpm = None

import generate_bindings
import run_tests


if not bdist_msi:
  BdistMSICommand = None
else:
  class BdistMSICommand(bdist_msi):
    """Custom handler for the bdist_msi command."""

    def run(self):
      """Builds an MSI."""
      # Command bdist_msi does not support the library version, neither a date
      # as a version but if we suffix it with .1 everything is fine.
      self.distribution.metadata.version += '.1'

      bdist_msi.run(self)


if not bdist_rpm:
  BdistRPMCommand = None
else:
  class BdistRPMCommand(bdist_rpm):
    """Custom handler for the bdist_rpm command."""

    def make_spec_file(self, spec_file):
      """Make an RPM Spec file."""
      if sys.version_info[0] < 3:
        python_package = "python"
      else:
        python_package = "python3"

      description = []
      summary = ""
      in_description = False

      python_spec_file = []
      for line in spec_file:
        if line.startswith("Summary: "):
          summary = line

        elif line.startswith("BuildRequires: "):
          line = "BuildRequires: {0}-setuptools".format(python_package)

        elif line.startswith('Requires: '):
          if python_package == 'python3':
            line = line.replace('python', 'python3')

        elif line.startswith("%description"):
          in_description = True

        elif line.startswith("%files"):
          line = "%files -f INSTALLED_FILES {0}".format(
              python_package)

        elif line.startswith("%prep"):
          in_description = False

          python_spec_file.append(
              "%package {0}".format(python_package))
          python_spec_file.append("{0}".format(summary))
          python_spec_file.append("")
          python_spec_file.append(
              "%description {0}".format(python_package))
          python_spec_file.extend(description)

        elif in_description:
          # Ignore leading white lines in the description.
          if not description and not line:
            continue

          description.append(line)

        python_spec_file.append(line)

      return python_spec_file

    def _make_spec_file(self):
      """Generates the text of an RPM spec file.

      Returns:
        list[str]: lines of text.
      """
      return self.make_spec_file(
          bdist_rpm._make_spec_file(self))


class BuildExtCommand(build_ext):
  """Custom handler for the build_ext command."""

  def configure_source_tree(self, compiler):
    """Configures the source and returns a dict of defines."""
    define_macros = []
    define_macros.append(("HAVE_TSK_LIBTSK_H", ""))

    if compiler.compiler_type == "msvc":
      return define_macros + [
          ("WIN32", "1"),
          ("UNICODE", "1"),
      ]

    # We want to build as much as possible self contained Python
    # binding.
    command = ["sh", "configure", "--disable-java", "--without-afflib",
               "--without-libewf", "--without-zlib"]

    output = subprocess.check_output(command, cwd="sleuthkit")
    print_line = False
    for line in output.split(b"\n"):
      line = line.rstrip()
      if line == b"configure:":
        print_line = True

      if print_line:
        if sys.version_info[0] >= 3:
          line = line.decode("ascii")
        print(line)

    return define_macros + [
        ("HAVE_CONFIG_H", "1"),
        ("LOCALEDIR", "\"/usr/share/locale\""),
    ]

  def run(self):
    compiler = new_compiler(compiler=self.compiler)
    # pylint: disable=attribute-defined-outside-init
    self.define = self.configure_source_tree(compiler)

    libtsk_path = os.path.join("sleuthkit", "tsk")

    if not os.access("pytsk3.c", os.R_OK):
      # Generate the Python binding code (pytsk3.c).
      libtsk_header_files = [
          os.path.join(libtsk_path, "libtsk.h"),
          os.path.join(libtsk_path, "base", "tsk_base.h"),
          os.path.join(libtsk_path, "fs", "tsk_fs.h"),
          os.path.join(libtsk_path, "img", "tsk_img.h"),
          os.path.join(libtsk_path, "vs", "tsk_vs.h"),
          "tsk3.h"]

      print("Generating bindings...")
      generate_bindings.generate_bindings(
          "pytsk3.c", libtsk_header_files, initialization="tsk_init();")

    build_ext.run(self)


class SDistCommand(sdist):
  """Custom handler for generating source dist."""
  def run(self):
    libtsk_path = os.path.join("sleuthkit", "tsk")

    # sleuthkit submodule is not there, probably because this has been
    # freshly checked out.
    if not os.access(libtsk_path, os.R_OK):
      subprocess.check_call(["git", "submodule", "init"])
      subprocess.check_call(["git", "submodule", "update"])

    if not os.path.exists(os.path.join("sleuthkit", "configure")):
      raise RuntimeError(
          "Missing: sleuthkit/configure run 'setup.py build' first.")

    sdist.run(self)


class UpdateCommand(Command):
  """Update sleuthkit source.

  This is normally only run by packagers to make a new release.
  """
  version = time.strftime("%Y%m%d")

  timezone_minutes, _ = divmod(time.timezone, 60)
  timezone_hours, timezone_minutes = divmod(timezone_minutes, 60)

  # If timezone_hours is -1 %02d will format as -1 instead of -01
  # hence we detect the sign and force a leading zero.
  if timezone_hours < 0:
    timezone_string = '-%02d%02d' % (-timezone_hours, timezone_minutes)
  else:
    timezone_string = '+%02d%02d' % (timezone_hours, timezone_minutes)

  version_pkg = '%s %s' % (
      time.strftime('%a, %d %b %Y %H:%M:%S'), timezone_string)

  user_options = []

  def initialize_options(self):
    pass

  def finalize_options(self):
    pass

  files = {
      "sleuthkit/configure.ac": [
          ("([a-z_/]+)/Makefile",
           lambda m: m.group(0) if m.group(1).startswith("tsk") else ""),
      ],
      "sleuthkit/Makefile.am": [
          ("SUBDIRS = .+", "SUBDIRS = tsk"),
      ],
      "class_parser.py": [
          ('VERSION = "[^"]+"', 'VERSION = "%s"' % version),
      ],
      "dpkg/changelog": [
          (r"pytsk3 \([^\)]+\)", "pytsk3 (%s-1)" % version),
          ("(<[^>]+>).+", r"\1  %s" % version_pkg),
      ],
      "sleuthkit/tsk/fs/fs_name.c": [
          ('#include "tsk_fs_i.h"', (
              '#include "tsk_fs_i.h"\n'
              '\n'
              '#include <time.h>\n'
              '\n'
              '#ifndef TZNAME\n'
              '#define TZNAME __tzname\n'
              '#endif')),
      ],
      "sleuthkit/tsk/fs/fs_open.c": [
          # Note that the list order is important here.
          ('const char \*name_first;', '/* const char \*name_first; */'),
          ('        const struct {', '        /* const struct {'),
          ('        };', '        }; */'),
          ('if \(a_img_info == NULL\) {', (
               'int i = 0;\n'
               '    const char *name_first;\n'
               '    const struct {\n'
               '        char* name;\n'
               '        TSK_FS_INFO* (*open)(TSK_IMG_INFO*, TSK_OFF_T,\n'
               '                             TSK_FS_TYPE_ENUM, uint8_t);\n'
               '        TSK_FS_TYPE_ENUM type;\n'
               '    } FS_OPENERS[] = {\n'
               '        { "NTFS",     ntfs_open,    TSK_FS_TYPE_NTFS_DETECT    },\n'
               '        { "FAT",      fatfs_open,   TSK_FS_TYPE_FAT_DETECT     },\n'
               '        { "EXT2/3/4", ext2fs_open,  TSK_FS_TYPE_EXT_DETECT     },\n'
               '        { "UFS",      ffs_open,     TSK_FS_TYPE_FFS_DETECT     },\n'
               '        { "YAFFS2",   yaffs2_open,  TSK_FS_TYPE_YAFFS2_DETECT  },\n'
               '#if TSK_USE_HFS\n'
               '        { "HFS",      hfs_open,     TSK_FS_TYPE_HFS_DETECT     },\n'
               '#endif\n'
               '        { "ISO9660",  iso9660_open, TSK_FS_TYPE_ISO9660_DETECT }\n'
               '    };\n'
               '\n'
               '    if (a_img_info == NULL) {')),
          ('for \(int i = 0;', 'for (i = 0;'),
      ],
      "sleuthkit/tsk/img/raw.c": [
          ('#include "raw.h"', (
              '#include "raw.h"\n'
              '\n'
              '#ifndef TSK_WIN32\n'
              '#include <sys/types.h>\n'
              '#include <sys/stat.h>\n'
              '#include <unistd.h>\n'
              '#include <fcntl.h>\n'
              '#endif\n'
              '\n'
              '#ifndef S_IFMT\n'
              '#define S_IFMT __S_IFMT\n'
              '#endif\n'
              '\n'
              '#ifndef S_IFDIR\n'
              '#define S_IFDIR __S_IFDIR\n'
              '#endif')),
      ],
  }

  def patch_sleuthkit(self):
    """Applies patches to the SleuthKit source code."""
    for filename, rules in iter(self.files.items()):
      filename = os.path.join(*filename.split("/"))

      with open(filename, "r") as file_object:
        data = file_object.read()

      for search, replace in rules:
        data = re.sub(search, replace, data)

      if filename == os.path.join("sleuthkit", "tsk", "img", "raw.c"):
        lines = data.split("\n")
        swap = lines.pop(381)
        lines.insert(372, swap)
        data = "\n".join(lines)

      with open(filename, "w") as fd:
        fd.write(data)

  def run(self):
    subprocess.check_call(["git", "stash"], cwd="sleuthkit")

    subprocess.check_call(["git", "submodule", "init"])
    subprocess.check_call(["git", "submodule", "update"])

    print("Updating sleuthkit")
    subprocess.check_call(["git", "reset", "--hard"], cwd="sleuthkit")
    subprocess.check_call(
        ["git", "clean", "-x", "-f", "-d"], cwd="sleuthkit")
    subprocess.check_call(["git", "checkout", "master"], cwd="sleuthkit")
    subprocess.check_call(["git", "pull"], cwd="sleuthkit")
    subprocess.check_call(["git", "fetch", "--tags"], cwd="sleuthkit")
    subprocess.check_call(
        ["git", "checkout", "tags/sleuthkit-4.4.2"], cwd="sleuthkit")

    self.patch_sleuthkit()

    compiler_type = distutils.ccompiler.get_default_compiler()
    if compiler_type != "msvc":
      subprocess.check_call(["./bootstrap"], cwd="sleuthkit")

    # Now derive the version based on the date.
    with open("version.txt", "w") as fd:
      fd.write(self.version)

    libtsk_path = os.path.join("sleuthkit", "tsk")

    # Generate the Python binding code (pytsk3.c).
    libtsk_header_files = [
        os.path.join(libtsk_path, "libtsk.h"),
        os.path.join(libtsk_path, "base", "tsk_base.h"),
        os.path.join(libtsk_path, "fs", "tsk_fs.h"),
        os.path.join(libtsk_path, "img", "tsk_img.h"),
        os.path.join(libtsk_path, "vs", "tsk_vs.h"),
        "tsk3.h"]

    print("Generating bindings...")
    generate_bindings.generate_bindings(
        "pytsk3.c", libtsk_header_files, initialization="tsk_init();")


class ProjectBuilder(object):
  """Class to help build the project."""

  def __init__(self, project_config, argv):
    """Initializes a project builder object."""
    self._project_config = project_config
    self._argv = argv

    # The path to the sleuthkit/tsk directory.
    self._libtsk_path = os.path.join("sleuthkit", "tsk")

    # Paths under the sleuthkit/tsk directory which contain files we need
    # to compile.
    self._sub_library_names = [
        "auto", "base", "docs", "fs", "hashdb", "img", "vs"]

    # The args for the extension builder.
    self.extension_args = {
        "define_macros": [],
        "include_dirs": ["talloc", self._libtsk_path, "sleuthkit", "."],
        "library_dirs": [],
        "libraries": []}

    # The sources to build.
    self._source_files = [
        "class.c", "error.c", "tsk3.c", "pytsk3.c", "talloc/talloc.c"]

    # Path to the top of the unpacked sleuthkit sources.
    self._sleuthkit_path = "sleuthkit"

  def build(self):
    """Build everything."""
    # Fetch all c and cpp files from the subdirs to compile.
    for library_name in self._sub_library_names:
      for extension in ("*.c", "*.cpp"):
        extension_glob = os.path.join(
            self._libtsk_path, library_name, extension)
        self._source_files.extend(glob.glob(extension_glob))

    # Sort the soure files to make sure they are in consistent order when
    # building.
    source_files = sorted(self._source_files)
    ext_modules = [Extension("pytsk3", source_files, **self.extension_args)]

    setup(
        cmdclass={
            "build_ext": BuildExtCommand,
            "bdist_msi": BdistMSICommand,
            "bdist_rpm": BdistRPMCommand,
            "sdist": SDistCommand,
            "update": UpdateCommand},
        ext_modules=ext_modules,
        **self._project_config)


if __name__ == "__main__":
  __version__ = open("version.txt").read().strip()

  setup_args = dict(
      name="pytsk3",
      version=__version__,
      description="Python bindings for the sleuthkit",
      long_description=(
          "Python bindings for the sleuthkit (http://www.sleuthkit.org/)"),
      license="Apache 2.0",
      url="https://github.com/py4n6/pytsk/",
      author="Michael Cohen and Joachim Metz",
      author_email="scudette@gmail.com, joachim.metz@gmail.com",
      zip_safe=False)

  ProjectBuilder(setup_args, sys.argv).build()
