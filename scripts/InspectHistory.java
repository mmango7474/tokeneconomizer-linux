// Static metadata and bounded decompilation for the compiled te-history helper.
// Run with analyzeHeadless ... -postScript InspectHistory.java /absolute/report.txt
//@category TokenEconomizer
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.mem.MemoryBlock;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.io.BufferedWriter;
import java.util.ArrayList;
import java.util.List;

public class InspectHistory extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected report path");
        try (BufferedWriter out = Files.newBufferedWriter(Path.of(args[0]), StandardCharsets.UTF_8)) {
            out.write("STATIC ANALYSIS ONLY; target was not executed.\n");
            out.write("Executable: " + currentProgram.getExecutablePath() + "\n");
            out.write("SHA256: " + currentProgram.getExecutableSHA256() + "\n");
            out.write("Format: " + currentProgram.getExecutableFormat() + "\n");
            out.write("Language: " + currentProgram.getLanguageID() + "\n");
            out.write("Compiler: " + currentProgram.getCompilerSpec().getCompilerSpecID() + "\n");
            out.write("Image base: " + currentProgram.getImageBase() + "\n");
            out.write("Function count: " + currentProgram.getFunctionManager().getFunctionCount() + "\n");
            for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
                out.write("Block: " + block.getName() + " " + block.getStart() + " bytes=" +
                    block.getSize() + " r=" + block.isRead() + " w=" + block.isWrite() +
                    " x=" + block.isExecute() + "\n");
            }
            List<Function> candidates = new ArrayList<>();
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            int names = 0;
            while (functions.hasNext() && !monitor.isCancelled()) {
                Function function = functions.next();
                String name = function.getName(true);
                if (name.contains("te_history")) {
                    if (names++ < 60) out.write("Scanner symbol: " + function.getEntryPoint() + " " + name + "\n");
                    if (!function.isThunk() && name.startsWith("te_history[") &&
                        (name.endsWith("::read_session") || name.endsWith("::identity") || name.endsWith("::scan"))) {
                        candidates.add(function);
                    }
                }
            }
            out.write("Matching symbols: " + names + "; name output capped at 60.\n");
            out.write("Decompilation candidates: " + candidates.size() + "; capped at 3, 30 seconds and 14000 chars each.\n");
            DecompInterface decompiler = new DecompInterface();
            try {
                if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
                int count = 0;
                for (Function function : candidates) {
                    if (count++ == 3 || monitor.isCancelled()) break;
                    out.write("\nFunction: " + function.getName(true) + " @ " + function.getEntryPoint() + "\n");
                    DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                    out.write("Completed: " + result.decompileCompleted() + "; diagnostics: " + result.getErrorMessage() + "\n");
                    if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                        String code = result.getDecompiledFunction().getC();
                        out.write("Original pseudocode chars: " + code.length() + "\n");
                        out.write(code.substring(0, Math.min(14000, code.length())));
                        if (code.length() > 14000) out.write("\n[TRUNCATED]\n");
                    }
                }
            } finally {
                decompiler.dispose();
            }
            out.write("\nPseudocode is inferred, not original Rust; static analysis does not prove runtime safety or performance.\n");
        }
        println("Wrote bounded static report: " + args[0]);
    }
}
