# This file is Copyright 2024 Volatility Foundation and licensed under the Volatility Software License 1.0
# which is available at https://www.volatilityfoundation.org/license/vsl-v1.0
#
import logging
from typing import List

from volatility3.framework import constants, exceptions, interfaces, renderers
from volatility3.framework.configuration import requirements
from volatility3.framework.symbols import intermed
from volatility3.framework.symbols.windows.extensions import pe
from volatility3.plugins.windows import pslist, modules

vollog = logging.getLogger(__name__)


class PEDump(interfaces.plugins.PluginInterface):
    """Allows extracting PE Files from a specific address in a specific address space"""

    _required_framework_version = (2, 0, 0)
    _version = (1, 0, 0)

    @classmethod
    def get_requirements(cls) -> List[interfaces.configuration.RequirementInterface]:
        # Since we're calling the plugin, make sure we have the plugin's requirements
        return [
            requirements.ModuleRequirement(
                name="kernel",
                description="Windows kernel",
                architectures=["Intel32", "Intel64"],
            ),
            requirements.VersionRequirement(
                name="pslist", component=pslist.PsList, version=(2, 0, 0)
            ),
            requirements.ListRequirement(
                name="pid",
                element_type=int,
                description="Process IDs to include (all other processes are excluded)",
                optional=True,
            ),
            requirements.IntRequirement(
                name="base",
                description="Base address to reconstruct a PE file",
                optional=False,
            ),
            requirements.BooleanRequirement(
                name="kernel_module",
                description="Extract from kernel address space.",
                default=False,
                optional=True,
            ),
        ]

    def _write_pe(self, pe_table_name, layer_name, proc_offset, pid):
        try:
            file_handle = self.open(
                "PE.{:#x}.{:d}.{:#x}.dmp".format(
                    proc_offset,
                    pid,
                    self.config["base"],
                )
            )

            dos_header = self.context.object(
                pe_table_name + constants.BANG + "_IMAGE_DOS_HEADER",
                offset=self.config["base"],
                layer_name=layer_name,
            )

            for offset, data in dos_header.reconstruct():
                file_handle.seek(offset)
                file_handle.write(data)
        except (
            IOError,
            exceptions.VolatilityException,
            OverflowError,
            ValueError,
        ) as excp:
            vollog.debug(f"Unable to dump dll at offset {self.config['base']}: {excp}")
            return None

        file_handle.close()
        return file_handle.preferred_filename

    def _dump_kernel(self, _, pe_table_name, session_layers):
        session_layer_name = modules.Modules.find_session_layer(
            self.context, session_layers, self.config["base"]
        )

        if session_layer_name:
            system_pid = 4

            file_output = self._write_pe(
                pe_table_name,
                session_layer_name,
                0,
                system_pid,
            )

            if file_output:
                yield system_pid, "Kernel", file_output
        else:
            vollog.warning(
                "Unable to find a session layer with the provided base address mapped in the kernel."
            )

    def _dump_processes(self, kernel, pe_table_name, _):
        filter_func = pslist.PsList.create_pid_filter(self.config.get("pid", None))
        kernel = self.context.modules[self.config["kernel"]]

        for proc in pslist.PsList.list_processes(
            context=self.context,
            layer_name=kernel.layer_name,
            symbol_table=kernel.symbol_table_name,
            filter_func=filter_func,
        ):
            pid = proc.UniqueProcessId
            proc_name = proc.ImageFileName.cast(
                "string",
                max_length=proc.ImageFileName.vol.count,
                errors="replace",
            )
            proc_layer_name = proc.add_process_layer()

            file_output = self._write_pe(
                pe_table_name,
                proc_layer_name,
                proc.vol.offset,
                pid,
            )

            if file_output:
                yield pid, proc_name, file_output

    def _generator(self):
        kernel = self.context.modules[self.config["kernel"]]

        pe_table_name = intermed.IntermediateSymbolTable.create(
            self.context, self.config_path, "windows", "pe", class_types=pe.class_types
        )

        if self.config["kernel_module"] and self.config["pid"]:
            vollog.error("Only --kernel_module or --pid should be set. Not both")
            return

        if not self.config["kernel_module"] and not self.config["pid"]:
            vollog.error("--kernel_module or --pid must be set")
            return

        if self.config["kernel_module"]:
            session_layers = modules.Modules.get_session_layers(
                self.context, kernel.layer_name, kernel.symbol_table_name
            )
            method = self._dump_kernel
        else:
            session_layers = None
            method = self._dump_processes

        for pid, proc_name, file_output in method(
            kernel, pe_table_name, session_layers
        ):
            yield (
                0,
                (
                    pid,
                    proc_name,
                    file_output,
                ),
            )

    def run(self):
        return renderers.TreeGrid(
            [
                ("PID", int),
                ("Process", str),
                ("File output", str),
            ],
            self._generator(),
        )
