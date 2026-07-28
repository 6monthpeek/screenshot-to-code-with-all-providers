from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam

from prompts.prompt_types import Stack
from prompts import system_prompt
from prompts.design_system import build_design_system_prompt_block
from prompts.policies import build_selected_stack_policy, build_user_image_policy

def build_image_prompt_messages(
    image_data_urls: list[str],
    stack: Stack,
    text_prompt: str,
    image_generation_enabled: bool,
    design_system: str | None = None,
) -> list[ChatCompletionMessageParam]:
    image_policy = build_user_image_policy(image_generation_enabled)
    selected_stack = build_selected_stack_policy(stack)
    design_system_block = build_design_system_prompt_block(design_system)

    # Only shown when there is more than one screenshot: the output is a
    # single file, so multiple pages must live behind hash-based navigation.
    multi_screenshot_block = ""
    if len(image_data_urls) > 1:
        multi_screenshot_block = f"""
## Multiple screenshots

You have been given {len(image_data_urls)} screenshots, numbered in the order provided (Screenshot 1 is the first image). Build ALL of them into this single file as separate pages/views:

- Give each screenshot its own page section and use hash-based navigation (e.g. `#/page-1`, `#/page-2`; listen for `hashchange` or use the framework's state) so every page is reachable through links.
- Show the page for Screenshot 1 by default when there is no hash.
- If the screenshots appear to be different pages of the same website or app, link them the way the real app would (nav bar links, buttons, cards) and keep shared elements like the header, nav and footer consistent across pages.
- If they appear unrelated, add a simple navigation scaffold labelled "Screenshot 1", "Screenshot 2", etc. to switch between them.
- For mobile screenshots, do not include the device frame or browser chrome; focus only on the actual UI mockups.
"""

    user_prompt = f"""
Generate code for a web page that looks exactly like the provided screenshot(s).

{selected_stack}
{design_system_block}

## Replication instructions

- Make sure the web page looks exactly like the screenshot.
- Use the exact text from the screenshot.
- Since our goal is to make the web page look as close to the screenshot as possible, we need to extract the exact image assets where possible and generate images for the assets that are not extractable.
- Extracting assets can be done with the extract_assets tool. After extracting assets, make sure to inspect the extracted image closely to ensure that it is what we want.
- When available, use edit_image for asset edits such as removing unwanted elements.
- If an extracted or supplied asset is visibly low-resolution or pixelated and must render larger, upscale it with edit_image—not CSS stretching or generate_images.
- If an asset in the original screenshot is not extractable (for example, occluded by other objects or is the background), when available, use generate_images to create image URLs from prompts (you may pass multiple prompts).

- {image_policy}
{multi_screenshot_block}"""

    # Add additional instructions provided by the user
    if text_prompt.strip():
        user_prompt = f"{user_prompt}\n\nAdditional instructions: {text_prompt}"

    user_content: list[ChatCompletionContentPartParam] = []
    for image_data_url in image_data_urls:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_data_url, "detail": "high"},
            }
        )
    user_content.append(
        {
            "type": "text",
            "text": user_prompt,
        }
    )
    return [
        {
            "role": "system",
            "content": system_prompt.build_system_prompt(stack),
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]
