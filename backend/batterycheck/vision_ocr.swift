import Foundation
import Vision
import ImageIO

// Local recognition only. No networking, image persistence, or language correction.
do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "OCR", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          CGImageSourceGetCount(source) == 1,
          let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = props[kCGImagePropertyPixelWidth] as? Int,
          let height = props[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width * height <= 20_000_000 else {
        throw NSError(domain: "InvalidImage", code: 2)
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let supported = try request.supportedRecognitionLanguages()
    request.recognitionLanguages = ["en-US", "ko-KR"].filter { supported.contains($0) }
    let handler = VNImageRequestHandler(url: url, options: [:])
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    let data = try JSONSerialization.data(withJSONObject: ["lines": lines])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("이미지를 인식하지 못했습니다.\n".utf8))
    exit(2)
}
